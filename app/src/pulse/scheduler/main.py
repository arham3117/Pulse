"""Scheduler: claim due monitors, enqueue one SQS message per check, advance next_check_at.

Several replicas run at once without leader election (ADR 0002): ``FOR UPDATE SKIP LOCKED``
makes each replica claim a disjoint set of rows. The rows stay locked until COMMIT, after
``next_check_at`` has moved forward, so no other replica can pick them up in between.

Failure case: SQS send succeeds but COMMIT fails → the next loop sends the same
``(monitor_id, scheduled_for)`` again. The worker's ``ON CONFLICT DO NOTHING`` on
``UNIQUE (monitor_id, scheduled_for)`` turns that duplicate into a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulse.core.config import Settings, get_settings
from pulse.core.db import create_engine, create_sessionmaker, db_ready
from pulse.core.health_server import HealthServer
from pulse.core.logging import configure_logging, get_logger
from pulse.core.metrics import (
    ACTIVE_MONITORS,
    QUEUE_MESSAGES,
    SCHED_DUE,
    SCHED_ENQUEUED,
    SCHED_LAG,
    SCHED_LOOP_DURATION,
    SCHEDULER_REGISTRY,
)
from pulse.core.models import Monitor
from pulse.core.queue import CheckMessage, SQSQueue

log = get_logger()

STATS_INTERVAL_SECONDS = 15

CLAIM_SQL = text(
    """
    SELECT id, interval_seconds, next_check_at, now() AS db_now
      FROM monitors
     WHERE is_active AND next_check_at <= now()
     ORDER BY next_check_at
     LIMIT :batch_size
       FOR UPDATE SKIP LOCKED
    """
)


def compute_next_check(previous: datetime, interval_seconds: int, now: datetime) -> datetime:
    """Advance by one interval, but never "catch up" with a burst after downtime."""
    nxt = previous + timedelta(seconds=interval_seconds)
    if nxt <= now:
        nxt = now + timedelta(seconds=interval_seconds)
    return nxt


@dataclass(frozen=True, slots=True)
class LoopResult:
    claimed: int
    enqueued: int
    lag_seconds: float


class SchedulerQueue:
    """The bit of SQSQueue the scheduler uses (lets tests pass a fake)."""

    async def send_checks(self, messages: list[CheckMessage]) -> list[CheckMessage]:
        raise NotImplementedError


async def schedule_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: SchedulerQueue | SQSQueue,
    batch_size: int,
) -> LoopResult:
    async with sessionmaker() as session, session.begin():
        rows = (await session.execute(CLAIM_SQL, {"batch_size": batch_size})).all()
        if not rows:
            return LoopResult(0, 0, 0.0)
        db_now: datetime = rows[0].db_now
        lag = max(0.0, (db_now - rows[0].next_check_at).total_seconds())
        messages = [CheckMessage(r.id, r.next_check_at) for r in rows]

        failed = await queue.send_checks(messages)
        failed_ids = {m.monitor_id for m in failed}
        if failed:
            log.warning("enqueue_partial_failure", failed=len(failed))

        for r in rows:
            if r.id in failed_ids:
                continue  # leave next_check_at alone; retried next loop
            await session.execute(
                update(Monitor)
                .where(Monitor.id == r.id)
                .values(
                    next_check_at=compute_next_check(r.next_check_at, r.interval_seconds, db_now)
                )
                .execution_options(synchronize_session=False)
            )
    enqueued = len(rows) - len(failed_ids)
    return LoopResult(len(rows), enqueued, lag)


async def collect_stats(
    sessionmaker: async_sessionmaker[AsyncSession], queue: SQSQueue, settings: Settings
) -> None:
    async with sessionmaker() as session:
        active = await session.scalar(
            select(func.count()).select_from(Monitor).where(Monitor.is_active)
        )
    ACTIVE_MONITORS.set(active or 0)
    for name, url in (("checks", settings.sqs_queue_url), ("dlq", settings.sqs_dlq_url)):
        if not url:
            continue
        visible, inflight = await queue.depth(url)
        QUEUE_MESSAGES.labels(queue=name, state="visible").set(visible)
        QUEUE_MESSAGES.labels(queue=name, state="inflight").set(inflight)


class Scheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stopping = asyncio.Event()

    async def run(self) -> None:
        settings = self.settings
        engine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        health = HealthServer(
            SCHEDULER_REGISTRY, lambda: db_ready(engine), port=settings.health_listen_port
        )
        await health.start()
        log.info(
            "scheduler_started",
            interval_seconds=settings.scheduler_interval_seconds,
            batch_size=settings.scheduler_batch_size,
        )
        last_stats = 0.0
        try:
            async with SQSQueue(settings) as queue:
                while not self.stopping.is_set():
                    started = time.monotonic()
                    full = False
                    try:
                        result = await schedule_once(
                            sessionmaker, queue, settings.scheduler_batch_size
                        )
                        SCHED_ENQUEUED.inc(result.enqueued)
                        SCHED_DUE.set(result.claimed)
                        SCHED_LAG.set(result.lag_seconds)
                        full = result.claimed >= settings.scheduler_batch_size
                        if result.claimed:
                            log.info(
                                "scheduler_loop",
                                claimed=result.claimed,
                                enqueued=result.enqueued,
                                lag_seconds=round(result.lag_seconds, 2),
                            )
                        if time.monotonic() - last_stats >= STATS_INTERVAL_SECONDS:
                            await collect_stats(sessionmaker, queue, settings)
                            last_stats = time.monotonic()
                    except Exception:
                        log.exception("scheduler_loop_failed")
                    finally:
                        SCHED_LOOP_DURATION.observe(time.monotonic() - started)
                    # A full batch means more work is waiting: loop again immediately.
                    if self.stopping.is_set():
                        break
                    if not full:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(
                                self.stopping.wait(), settings.scheduler_interval_seconds
                            )
        finally:
            await health.stop()
            await engine.dispose()
            log.info("scheduler_stopped")


def run() -> None:
    settings = get_settings()
    configure_logging("scheduler", settings.log_level)
    scheduler = Scheduler(settings)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # Finish the current loop (it's one transaction), then exit.
            loop.add_signal_handler(sig, scheduler.stopping.set)
        await scheduler.run()

    asyncio.run(main())


if __name__ == "__main__":
    run()
