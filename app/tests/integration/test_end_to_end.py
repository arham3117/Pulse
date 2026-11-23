"""Phase 1.10 end-to-end flow with real Postgres, ElasticMQ and a real HTTP chaos target:

create monitor via API → scheduler enqueues → worker checks pulse-target → result stored →
switch target down → incident opens after 3 failures → notifier emits event →
switch target up → incident resolves.
"""

import asyncio
import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select, text, update

from pulse.api.main import create_app
from pulse.core.metrics import WORKER_MESSAGE_ERRORS
from pulse.core.models import CheckResult, Incident, Monitor
from pulse.core.notifier import EventKind, NotificationEvent, Notifier
from pulse.core.queue import CheckMessage, SQSQueue
from pulse.scheduler.main import schedule_once
from pulse.target.main import create_app as create_target
from pulse.worker.checker import build_http_client
from pulse.worker.main import CheckProcessor, Worker
from tests.conftest import ADMIN_KEY

pytestmark = pytest.mark.integration


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    async def _send(self, event: NotificationEvent) -> None:
        self.events.append(event)


async def no_tls(host: str, port: int) -> None:
    return None


@pytest.fixture
async def target_url():
    """Run the real chaos target over HTTP on a free localhost port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_target(), host="127.0.0.1", port=port, log_config=None, access_log=False
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://localhost:{port}"
    server.should_exit = True
    await task


async def _cycle(sessionmaker, queue: SQSQueue, worker: Worker, processor: CheckProcessor) -> int:
    """Make every monitor due, run one scheduler loop, then let the worker drain the queue."""
    async with sessionmaker() as s, s.begin():
        # Pretend an interval has passed (keeps the test fast and scheduled_for unique).
        await s.execute(
            update(Monitor).values(next_check_at=Monitor.next_check_at - timedelta(minutes=2))
        )
    result = await schedule_once(sessionmaker, queue, batch_size=100)
    handled = 0
    while handled < result.enqueued:
        messages = await queue.receive(max_messages=10, wait_seconds=1)
        for received in messages:
            await worker.semaphore.acquire()
            await worker.handle(queue, processor, received)
            handled += 1
    return handled


async def test_full_flow(engine, sessionmaker, settings, sqs_queue_urls, target_url) -> None:
    main_url, dlq_url = sqs_queue_urls
    settings = settings.model_copy(update={"sqs_queue_url": main_url, "sqs_dlq_url": dlq_url})
    notifier = RecordingNotifier()
    http = build_http_client(10)
    api_app = create_app(settings)
    async with (
        api_app.router.lifespan_context(api_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://api") as api,
        httpx.AsyncClient(base_url=target_url) as target,
        SQSQueue(settings) as queue,
    ):
        # 1. Create monitor via the API.
        resp = await api.post(
            "/api/v1/monitors",
            json={
                "name": "Switch",
                "url": f"{target_url}/switch",
                "keyword": "PULSE_OK",
                "interval_seconds": 30,
                "failure_threshold": 3,
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 201, resp.text
        monitor_id = resp.json()["id"]

        worker = Worker(settings)
        processor = CheckProcessor(settings, sessionmaker, http, notifier, tls_fetcher=no_tls)

        # 2-4. Scheduler enqueues, worker checks the target, result stored, status up.
        assert await _cycle(sessionmaker, queue, worker, processor) == 1
        detail = (await api.get(f"/api/v1/monitors/{monitor_id}")).json()
        assert detail["status"] == "up"
        results = (await api.get(f"/api/v1/monitors/{monitor_id}/results")).json()["items"]
        assert len(results) == 1 and results[0]["success"] and results[0]["status_code"] == 200

        # 5-6. Switch the target down: two failures keep status, the third opens an incident.
        await target.post("/switch/down")
        for expected_failures in (1, 2):
            await _cycle(sessionmaker, queue, worker, processor)
            detail = (await api.get(f"/api/v1/monitors/{monitor_id}")).json()
            assert detail["consecutive_failures"] == expected_failures
            assert detail["status"] == "up"
        assert notifier.events == []
        await _cycle(sessionmaker, queue, worker, processor)
        detail = (await api.get(f"/api/v1/monitors/{monitor_id}")).json()
        assert detail["status"] == "down"
        incidents = (await api.get("/api/v1/incidents", params={"open": "true"})).json()
        assert len(incidents) == 1
        assert incidents[0]["notified_open_at"] is not None
        assert incidents[0]["first_error"].startswith("HTTP 503")

        # 7. Notification emitted for the opened incident.
        assert [e.kind for e in notifier.events] == [EventKind.INCIDENT_OPENED]
        assert notifier.events[0].consecutive_failures == 3

        # 8. Switch up → incident resolves with a downtime duration.
        await target.post("/switch/up")
        await _cycle(sessionmaker, queue, worker, processor)
        detail = (await api.get(f"/api/v1/monitors/{monitor_id}")).json()
        assert detail["status"] == "up" and detail["consecutive_failures"] == 0
        assert (await api.get("/api/v1/incidents", params={"open": "true"})).json() == []
        assert [e.kind for e in notifier.events] == [
            EventKind.INCIDENT_OPENED,
            EventKind.INCIDENT_RESOLVED,
        ]
        assert notifier.events[1].downtime is not None

        # Status page reflects it.
        page = await api.get("/")
        assert "Switch" in page.text and "All systems operational" in page.text

        # Queue fully drained: every message was deleted after commit.
        visible, inflight = await queue.depth(main_url)
        assert (visible, inflight) == (0, 0)
    await http.aclose()


async def test_duplicate_message_is_idempotent(engine, sessionmaker, settings, target_url) -> None:
    async with sessionmaker() as s, s.begin():
        m = Monitor(name="dup", url=f"{target_url}/ok")
        s.add(m)
    http = build_http_client(5)
    processor = CheckProcessor(
        settings, sessionmaker, http, RecordingNotifier(), tls_fetcher=no_tls
    )
    msg = CheckMessage(m.id, datetime.now(UTC).replace(microsecond=0))
    first, second = await asyncio.gather(processor.process(msg), processor.process(msg))
    assert sorted([first.action, second.action]) == ["checked", "duplicate"]
    async with sessionmaker() as s:
        count = await s.scalar(select(func.count()).select_from(CheckResult))
    assert count == 1
    await http.aclose()


async def test_missing_or_paused_monitor_is_skipped(engine, sessionmaker, settings) -> None:
    import uuid

    processor = CheckProcessor(settings, sessionmaker, build_http_client(1), RecordingNotifier())
    result = await processor.process(CheckMessage(uuid.uuid4(), datetime.now(UTC)))
    assert result.action == "skipped"
    async with sessionmaker() as s, s.begin():
        m = Monitor(name="paused", url="http://localhost/", is_active=False, status="paused")
        s.add(m)
    assert (await processor.process(CheckMessage(m.id, datetime.now(UTC)))).action == "skipped"


async def test_poison_message_goes_to_dlq(engine, sessionmaker, settings, sqs_queue_urls) -> None:
    main_url, dlq_url = sqs_queue_urls
    settings = settings.model_copy(update={"sqs_queue_url": main_url, "sqs_dlq_url": dlq_url})
    worker = Worker(settings)
    processor = CheckProcessor(settings, sessionmaker, build_http_client(1), RecordingNotifier())
    before = WORKER_MESSAGE_ERRORS._value.get()
    async with SQSQueue(settings) as queue:
        await queue._client.send_message(QueueUrl=main_url, MessageBody="{not json")
        receives = 0
        for _ in range(20):  # visibility timeout is 1s on test queues
            for received in await queue.receive(max_messages=1, wait_seconds=1):
                receives += 1
                await worker.semaphore.acquire()
                await worker.handle(queue, processor, received)
            if (await queue.depth(dlq_url))[0] == 1:
                break
        assert receives == 3
        assert await queue.depth(dlq_url) == (1, 0)
    assert WORKER_MESSAGE_ERRORS._value.get() - before == 3


async def test_tls_expiry_notification_once_per_day(
    engine, sessionmaker, settings, target_url
) -> None:
    soon = datetime.now(UTC) + timedelta(days=5)

    async def expiring(host: str, port: int) -> datetime:
        return soon

    async with sessionmaker() as s, s.begin():
        # https URL that is actually served by the plain-HTTP target is fine: we only care
        # about the TLS bookkeeping here, so point the check at the target and fake expiry.
        m = Monitor(name="tls", url=f"{target_url}/ok")
        s.add(m)
    async with sessionmaker() as s, s.begin():
        await s.execute(
            text("UPDATE monitors SET url = replace(url, 'http://', 'https://') WHERE id = :id"),
            {"id": m.id},
        )
    notifier = RecordingNotifier()
    http = build_http_client(1)
    processor = CheckProcessor(settings, sessionmaker, http, notifier, tls_fetcher=expiring)
    now = datetime.now(UTC)
    await processor.process(CheckMessage(m.id, now))
    await processor.process(CheckMessage(m.id, now + timedelta(seconds=30)))
    tls_events = [e for e in notifier.events if e.kind is EventKind.TLS_EXPIRING]
    assert len(tls_events) == 1
    async with sessionmaker() as s:
        row = await s.get(Monitor, m.id)
        assert row is not None and row.tls_expires_at == soon and row.tls_notified_at is not None
    incidents = 0
    async with sessionmaker() as s:
        incidents = await s.scalar(select(func.count()).select_from(Incident))
    assert incidents == 0
    await http.aclose()
