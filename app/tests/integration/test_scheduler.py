"""Scheduler correctness against real Postgres.

The concurrency test runs two schedulers against the same database and asserts that no
(monitor_id, scheduled_for) pair is ever enqueued twice. The plan asks for 60 seconds;
set PULSE_TEST_SCHEDULER_SECONDS=60 for the full run (default is shorter to keep CI fast).
"""

import asyncio
import os
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text, update

from pulse.core.models import Monitor
from pulse.core.queue import CheckMessage, SQSQueue
from pulse.scheduler.main import SchedulerQueue, collect_stats, schedule_once

pytestmark = pytest.mark.integration


class RecordingQueue(SchedulerQueue):
    def __init__(self, sent: list[CheckMessage], fail_every: int = 0) -> None:
        self.sent = sent
        self.fail_every = fail_every
        self.calls = 0

    async def send_checks(self, messages: list[CheckMessage]) -> list[CheckMessage]:
        self.calls += 1
        await asyncio.sleep(random.uniform(0, 0.02))  # widen the race window
        failed = messages[:1] if self.fail_every and self.calls % self.fail_every == 0 else []
        self.sent.extend(m for m in messages if m not in failed)
        return failed


async def _add_monitors(sessionmaker, n: int, interval: int = 30, due_in: float = -1.0) -> None:
    now = datetime.now(UTC)
    async with sessionmaker() as s, s.begin():
        await s.execute(
            insert(Monitor),
            [
                {
                    "name": f"m{i}",
                    "url": f"http://localhost:8080/ok?i={i}",
                    "interval_seconds": interval,
                    "next_check_at": now + timedelta(seconds=due_in - random.random()),
                }
                for i in range(n)
            ],
        )


async def test_schedule_once_claims_due_and_advances(sessionmaker) -> None:
    await _add_monitors(sessionmaker, 5)
    await _add_monitors(sessionmaker, 3, due_in=600)  # not due
    async with sessionmaker() as s, s.begin():
        await s.execute(update(Monitor).where(Monitor.name == "m0").values(is_active=False))
    sent: list[CheckMessage] = []
    result = await schedule_once(sessionmaker, RecordingQueue(sent), batch_size=100)
    assert result.claimed == 4 and result.enqueued == 4 and len(sent) == 4
    assert result.lag_seconds >= 0
    again = await schedule_once(sessionmaker, RecordingQueue(sent), batch_size=100)
    assert again.claimed == 0  # next_check_at moved into the future
    async with sessionmaker() as s:
        nexts = (await s.scalars(select(Monitor.next_check_at).where(Monitor.is_active))).all()
    assert all(n > datetime.now(UTC) for n in nexts)


async def test_failed_sends_are_retried_next_loop(sessionmaker) -> None:
    await _add_monitors(sessionmaker, 3)
    sent: list[CheckMessage] = []
    q = RecordingQueue(sent, fail_every=1)
    first = await schedule_once(sessionmaker, q, batch_size=100)
    assert first.enqueued == 2
    second = await schedule_once(sessionmaker, RecordingQueue(sent), batch_size=100)
    assert second.claimed == 1  # the failed one is still due
    assert len({m.monitor_id for m in sent}) == 3


async def test_two_schedulers_never_double_enqueue(sessionmaker) -> None:
    duration = float(os.environ.get("PULSE_TEST_SCHEDULER_SECONDS", "8"))
    await _add_monitors(sessionmaker, 400, interval=30)
    sent: list[CheckMessage] = []
    stop = asyncio.Event()

    async def scheduler_loop() -> None:
        q = RecordingQueue(sent)
        while not stop.is_set():
            await schedule_once(sessionmaker, q, batch_size=37)
            # Keep work flowing: make some monitors due again with a *new* scheduled_for
            # (simulates intervals passing). clock_timestamp() differs per statement, so the
            # new value never equals a scheduled_for that was already sent.
            async with sessionmaker() as s, s.begin():
                await s.execute(
                    text(
                        "UPDATE monitors "
                        "SET next_check_at = clock_timestamp() - interval '1 second' "
                        "WHERE id IN (SELECT id FROM monitors ORDER BY random() LIMIT 10 "
                        "FOR UPDATE SKIP LOCKED)"
                    )
                )
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(scheduler_loop()) for _ in range(2)]
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*tasks)

    counts = Counter((m.monitor_id, m.scheduled_for) for m in sent)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    assert len(sent) >= 400
    assert duplicates == {}


async def test_collect_stats_with_elasticmq(sessionmaker, settings, sqs_queue_urls) -> None:
    main, dlq = sqs_queue_urls
    await _add_monitors(sessionmaker, 2)
    s = settings.model_copy(update={"sqs_queue_url": main, "sqs_dlq_url": dlq})
    async with SQSQueue(s) as q:
        result = await schedule_once(sessionmaker, q, batch_size=10)
        assert result.enqueued == 2
        await collect_stats(sessionmaker, q, s)
    from pulse.core.metrics import ACTIVE_MONITORS, QUEUE_MESSAGES

    assert ACTIVE_MONITORS._value.get() == 2
    assert QUEUE_MESSAGES.labels(queue="checks", state="visible")._value.get() == 2
    assert QUEUE_MESSAGES.labels(queue="dlq", state="visible")._value.get() == 0
