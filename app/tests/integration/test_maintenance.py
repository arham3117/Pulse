from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from pulse.core.models import CheckResult, Monitor
from pulse.maintenance.main import purge_old_results

pytestmark = pytest.mark.integration


async def test_retention_deletes_only_old_rows_in_batches(sessionmaker) -> None:
    now = datetime.now(UTC)
    async with sessionmaker() as s, s.begin():
        m = Monitor(name="r", url="http://localhost/")
        s.add(m)
        await s.flush()
        await s.execute(
            insert(CheckResult),
            [
                {
                    "monitor_id": m.id,
                    "scheduled_for": now - timedelta(days=d, minutes=i),
                    "checked_at": now - timedelta(days=d, minutes=i),
                    "success": True,
                    "worker_id": "t",
                }
                for d in (1, 40)
                for i in range(25)
            ],
        )
    deleted = await purge_old_results(sessionmaker, retention_days=30, batch_size=10)
    assert deleted == 25
    async with sessionmaker() as s:
        remaining = await s.scalar(select(func.count()).select_from(CheckResult))
    assert remaining == 25
