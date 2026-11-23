"""Worker: long-poll SQS, run checks, store results, drive incidents, notify.

Delivery guarantees (interview topic "what if a worker crashes mid-check?"):
  * The SQS message is deleted only after the DB transaction commits. A crash before that
    means SQS redelivers the message after the visibility timeout (60s).
  * ``UNIQUE (monitor_id, scheduled_for)`` + ``ON CONFLICT DO NOTHING`` makes a redelivered
    or duplicated message harmless: the second insert is a no-op and we skip state changes.
  * A message that keeps failing (e.g. malformed JSON) is received 3 times, then SQS moves
    it to the DLQ (``maxReceiveCount = 3``) and ``PulseDLQNotEmpty`` fires.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pulse.core.config import Settings, get_settings
from pulse.core.db import create_engine, create_sessionmaker, db_ready
from pulse.core.health_server import HealthServer
from pulse.core.logging import configure_logging, get_logger
from pulse.core.metrics import (
    CHECK_DURATION,
    CHECK_SCHEDULE_LAG,
    CHECKS,
    INCIDENTS_OPENED,
    WORKER_INFLIGHT,
    WORKER_MESSAGE_ERRORS,
    WORKER_REGISTRY,
)
from pulse.core.models import CheckResult, Incident, Monitor
from pulse.core.notifier import EventKind, NotificationEvent, Notifier, build_notifier
from pulse.core.queue import CheckMessage, ReceivedMessage, SQSQueue
from pulse.core.url_safety import Resolver, system_resolver
from pulse.worker.checker import (
    CheckOutcome,
    CheckSpec,
    build_http_client,
    fetch_tls_expiry,
    run_check,
)
from pulse.worker.state import MonitorState, Transition, apply_check

log = get_logger()

TLS_RECHECK_INTERVAL = timedelta(hours=6)
TLS_WARN_WINDOW = timedelta(days=14)
TLS_NOTIFY_EVERY = timedelta(days=1)
SHUTDOWN_GRACE_SECONDS = 25


@dataclass
class ProcessResult:
    """What happened to one message; returned for tests and logging."""

    action: str  # checked | duplicate | skipped
    outcome: CheckOutcome | None = None
    events: list[NotificationEvent] = field(default_factory=list)


class CheckProcessor:
    """Processes one check message end to end (minus the SQS delete)."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        notifier: Notifier,
        resolver: Resolver = system_resolver,
        tls_fetcher=fetch_tls_expiry,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.http = http
        self.notifier = notifier
        self.resolver = resolver
        self.tls_fetcher = tls_fetcher

    async def process(self, msg: CheckMessage) -> ProcessResult:
        # 1. Load the monitor (no lock yet: the HTTP check can take seconds).
        async with self.sessionmaker() as session:
            monitor = await session.get(Monitor, msg.monitor_id)
            if monitor is None or not monitor.is_active:
                return ProcessResult("skipped")
            spec = CheckSpec(
                url=monitor.url,
                method=monitor.method,
                timeout_ms=monitor.timeout_ms,
                expected_status_min=monitor.expected_status_min,
                expected_status_max=monitor.expected_status_max,
                keyword=monitor.keyword,
            )
            tls_due = monitor.url.lower().startswith("https://") and (
                monitor.tls_checked_at is None
                or datetime.now(UTC) - monitor.tls_checked_at > TLS_RECHECK_INTERVAL
            )

        # 2-4. SSRF validation + HTTP check + classification.
        outcome = await run_check(self.http, spec, self.settings.allow_private_hosts, self.resolver)
        checked_at = datetime.now(UTC)

        # 5. TLS expiry (at most every 6 hours per monitor).
        tls_expires_at: datetime | None = None
        if tls_due and outcome.error_type != "blocked":
            host = httpx.URL(spec.url).host
            port = httpx.URL(spec.url).port or 443
            tls_expires_at = await self.tls_fetcher(host, port)

        # 6. One transaction: insert result (idempotent), lock monitor, update state/incidents.
        events: list[NotificationEvent] = []
        opened_id: uuid.UUID | None = None
        resolved_id: uuid.UUID | None = None
        tls_event: NotificationEvent | None = None
        async with self.sessionmaker() as session, session.begin():
            monitor = await session.get(Monitor, msg.monitor_id, with_for_update=True)
            if monitor is None or not monitor.is_active:
                return ProcessResult("skipped", outcome)

            inserted = await session.scalar(
                insert(CheckResult)
                .values(
                    monitor_id=msg.monitor_id,
                    scheduled_for=msg.scheduled_for,
                    checked_at=checked_at,
                    success=outcome.success,
                    status_code=outcome.status_code,
                    response_time_ms=outcome.response_time_ms,
                    error_type=outcome.error_type,
                    error_message=outcome.error_message,
                    worker_id=self.settings.worker_id,
                )
                .on_conflict_do_nothing(constraint="uq_check_results_monitor_sched")
                .returning(CheckResult.id)
            )
            if inserted is None:
                return ProcessResult("duplicate", outcome)

            open_incident = await session.scalar(
                select(Incident).where(
                    Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None)
                )
            )
            before = MonitorState(
                status=monitor.status,
                consecutive_failures=monitor.consecutive_failures,
                failing_since=monitor.failing_since,
                failing_error=monitor.failing_error,
                has_open_incident=open_incident is not None,
            )
            result = apply_check(
                before,
                success=outcome.success,
                checked_at=checked_at,
                error=outcome.summary,
                failure_threshold=monitor.failure_threshold,
            )
            after = result.state
            monitor.status = after.status
            monitor.consecutive_failures = after.consecutive_failures
            monitor.failing_since = after.failing_since
            monitor.failing_error = after.failing_error
            monitor.last_checked_at = checked_at

            if result.transition is Transition.OPEN_INCIDENT:
                incident = Incident(
                    monitor_id=monitor.id,
                    started_at=after.failing_since or checked_at,
                    first_error=after.failing_error or outcome.summary,
                )
                session.add(incident)
                await session.flush()
                opened_id = incident.id
                events.append(
                    NotificationEvent(
                        kind=EventKind.INCIDENT_OPENED,
                        monitor_name=monitor.name,
                        monitor_url=monitor.url,
                        occurred_at=checked_at,
                        error=outcome.summary,
                        consecutive_failures=after.consecutive_failures,
                        started_at=incident.started_at,
                    )
                )
            elif result.transition is Transition.RESOLVE_INCIDENT and open_incident is not None:
                open_incident.resolved_at = checked_at
                resolved_id = open_incident.id
                events.append(
                    NotificationEvent(
                        kind=EventKind.INCIDENT_RESOLVED,
                        monitor_name=monitor.name,
                        monitor_url=monitor.url,
                        occurred_at=checked_at,
                        started_at=open_incident.started_at,
                        downtime=checked_at - open_incident.started_at,
                    )
                )

            if tls_due:
                monitor.tls_checked_at = checked_at
                if tls_expires_at is not None:
                    monitor.tls_expires_at = tls_expires_at
            if (
                monitor.tls_expires_at is not None
                and monitor.tls_expires_at - checked_at < TLS_WARN_WINDOW
                and (
                    monitor.tls_notified_at is None
                    or checked_at - monitor.tls_notified_at > TLS_NOTIFY_EVERY
                )
            ):
                # Claim the daily slot inside the transaction so two workers can't both send.
                monitor.tls_notified_at = checked_at
                tls_event = NotificationEvent(
                    kind=EventKind.TLS_EXPIRING,
                    monitor_name=monitor.name,
                    monitor_url=monitor.url,
                    occurred_at=checked_at,
                    tls_expires_at=monitor.tls_expires_at,
                )
                events.append(tls_event)
            monitor_id_for_log = monitor.id

        # Metrics only for results we actually stored.
        CHECKS.labels(
            result="success" if outcome.success else "failure",
            error_type=outcome.error_type or "none",
        ).inc()
        if outcome.response_time_ms is not None:
            CHECK_DURATION.observe(outcome.response_time_ms / 1000)
        CHECK_SCHEDULE_LAG.observe(max(0.0, (checked_at - msg.scheduled_for).total_seconds()))
        if opened_id:
            INCIDENTS_OPENED.inc()
        log.info(
            "check_completed",
            monitor_id=str(monitor_id_for_log),
            success=outcome.success,
            status_code=outcome.status_code,
            response_time_ms=outcome.response_time_ms,
            error_type=outcome.error_type,
            transition=result.transition.value,
        )

        # 7. Notifications after commit; record delivery time.
        for event in events:
            delivered = await self.notifier.notify(event)
            if not delivered:
                continue
            column = None
            incident_id = None
            if event.kind is EventKind.INCIDENT_OPENED:
                column, incident_id = "notified_open_at", opened_id
            elif event.kind is EventKind.INCIDENT_RESOLVED:
                column, incident_id = "notified_resolved_at", resolved_id
            if column and incident_id:
                async with self.sessionmaker() as session, session.begin():
                    await session.execute(
                        update(Incident)
                        .where(Incident.id == incident_id)
                        .values({column: datetime.now(UTC)})
                    )
        return ProcessResult("checked", outcome, events)


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stopping = asyncio.Event()
        self.semaphore = asyncio.Semaphore(settings.worker_concurrency)
        self.inflight: set[asyncio.Task[None]] = set()
        self.health: HealthServer | None = None  # set by run(); exposed for tests

    async def handle(
        self, queue: SQSQueue, processor: CheckProcessor, received: ReceivedMessage
    ) -> None:
        WORKER_INFLIGHT.inc()
        try:
            msg = CheckMessage.from_body(received.body)
            await processor.process(msg)
            await queue.delete(received.receipt_handle)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Leave the message: SQS redelivers it, and after 3 receives it goes to the DLQ.
            WORKER_MESSAGE_ERRORS.inc()
            log.exception(
                "message_failed",
                message_id=received.message_id,
                receive_count=received.receive_count,
            )
        finally:
            WORKER_INFLIGHT.dec()
            self.semaphore.release()

    async def run(self) -> None:
        settings = self.settings
        engine: AsyncEngine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        http = build_http_client(settings.worker_concurrency)
        notifier = build_notifier(settings.alert_webhook_url.get_secret_value() or None)
        health = HealthServer(
            WORKER_REGISTRY, lambda: db_ready(engine), port=settings.health_listen_port
        )
        await health.start()
        self.health = health
        log.info("worker_started", concurrency=settings.worker_concurrency)
        try:
            async with SQSQueue(settings) as queue:
                processor = CheckProcessor(settings, sessionmaker, http, notifier)
                await self._receive_loop(queue, processor)
                await self._drain()
        finally:
            await notifier.aclose()
            await http.aclose()
            await health.stop()
            await engine.dispose()
            log.info("worker_stopped")

    async def _receive_loop(self, queue: SQSQueue, processor: CheckProcessor) -> None:
        stop_wait = asyncio.create_task(self.stopping.wait())
        try:
            while not self.stopping.is_set():
                await self.semaphore.acquire()  # wait for at least one free slot
                free = 1
                while free < 10 and not self.semaphore.locked():
                    await self.semaphore.acquire()
                    free += 1
                receive = asyncio.create_task(queue.receive(max_messages=free, wait_seconds=20))
                done, _ = await asyncio.wait(
                    {receive, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if receive not in done:
                    receive.cancel()
                    for _ in range(free):
                        self.semaphore.release()
                    break
                try:
                    messages = receive.result()
                except Exception:
                    log.exception("receive_failed")
                    messages = []
                    await asyncio.sleep(1)
                for _ in range(free - len(messages)):
                    self.semaphore.release()
                for received in messages:
                    task = asyncio.create_task(self.handle(queue, processor, received))
                    self.inflight.add(task)
                    task.add_done_callback(self.inflight.discard)
        finally:
            stop_wait.cancel()

    async def _drain(self) -> None:
        if not self.inflight:
            return
        log.info("worker_draining", inflight=len(self.inflight))
        _done, pending = await asyncio.wait(self.inflight, timeout=SHUTDOWN_GRACE_SECONDS)
        for task in pending:
            task.cancel()  # message stays on the queue and is redelivered
        if pending:
            log.warning("worker_drain_timeout", abandoned=len(pending))


def run() -> None:
    settings = get_settings()
    configure_logging("worker", settings.log_level)
    worker = Worker(settings)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stopping.set)
        await worker.run()

    asyncio.run(main())


if __name__ == "__main__":
    run()
