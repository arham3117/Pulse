"""Retention cleanup: delete check_results older than PULSE_RETENTION_DAYS.

Deletes in batches of 10,000 rows, each in its own short transaction, so we never hold a
giant lock or generate one huge WAL burst. Runs daily as a Kubernetes CronJob.
(Partitioning check_results by day and dropping partitions is a Phase 10 stretch goal.)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulse.core.config import get_settings
from pulse.core.db import create_engine, create_sessionmaker
from pulse.core.logging import configure_logging, get_logger

log = get_logger()

BATCH_SIZE = 10_000

DELETE_BATCH_SQL = text(
    """
    DELETE FROM check_results
     WHERE id IN (
        SELECT id FROM check_results
         WHERE checked_at < :cutoff
         LIMIT :batch
     )
    """
)


async def purge_old_results(
    sessionmaker: async_sessionmaker[AsyncSession],
    retention_days: int,
    batch_size: int = BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    total = 0
    while True:
        async with sessionmaker() as session, session.begin():
            result = await session.execute(
                DELETE_BATCH_SQL, {"cutoff": cutoff, "batch": batch_size}
            )
        deleted = result.rowcount or 0  # type: ignore[attr-defined]
        total += deleted
        if deleted:
            log.info("retention_batch_deleted", deleted=deleted, total=total)
        if deleted < batch_size:
            return total


async def main() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        total = await purge_old_results(create_sessionmaker(engine), settings.retention_days)
        log.info("retention_done", deleted=total, retention_days=settings.retention_days)
        return 0
    except Exception:
        log.exception("retention_failed")
        return 1
    finally:
        await engine.dispose()


def run() -> None:
    settings = get_settings()
    configure_logging("maintenance", settings.log_level)
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    run()
