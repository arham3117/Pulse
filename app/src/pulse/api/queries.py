"""Read-side SQL shared by the JSON API and the HTML status page.

Aggregations run in Postgres (``FILTER`` clauses + ``percentile_cont``) so the API never
pulls thousands of rows into Python.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATS_SQL = text(
    """
    SELECT monitor_id,
           100.0 * avg(success::int) FILTER (WHERE checked_at > now() - interval '24 hours') AS uptime_24h,
           100.0 * avg(success::int) FILTER (WHERE checked_at > now() - interval '7 days')   AS uptime_7d,
           100.0 * avg(success::int)                                                          AS uptime_30d,
           avg(response_time_ms) FILTER (WHERE success AND checked_at > now() - interval '24 hours')
               AS avg_response_ms_24h,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY response_time_ms)
               FILTER (WHERE success AND checked_at > now() - interval '24 hours')
               AS p95_response_ms_24h,
           count(*) FILTER (WHERE checked_at > now() - interval '24 hours') AS checks_24h
      FROM check_results
     WHERE monitor_id = ANY(:ids)
       AND checked_at > now() - interval '30 days'
     GROUP BY monitor_id
    """
)

RECENT_SQL = text(
    """
    SELECT m.id AS monitor_id, r.success, r.checked_at, r.response_time_ms, r.status_code,
           r.error_type
      FROM unnest(CAST(:ids AS uuid[])) AS m(id)
      CROSS JOIN LATERAL (
            SELECT success, checked_at, response_time_ms, status_code, error_type
              FROM check_results
             WHERE monitor_id = m.id
             ORDER BY checked_at DESC
             LIMIT :n
      ) r
    """
)

LATENCY_24H_SQL = text(
    """
    SELECT checked_at, response_time_ms, success
      FROM check_results
     WHERE monitor_id = :id AND checked_at > now() - interval '24 hours'
     ORDER BY checked_at
    """
)

NEWEST_RESULT_SQL = text("SELECT max(checked_at) FROM check_results")


@dataclass
class Stats:
    uptime_24h: float | None = None
    uptime_7d: float | None = None
    uptime_30d: float | None = None
    avg_response_ms_24h: float | None = None
    p95_response_ms_24h: float | None = None
    checks_24h: int = 0


@dataclass
class RecentCheck:
    success: bool
    checked_at: datetime
    response_time_ms: int | None
    status_code: int | None
    error_type: str | None


@dataclass
class MonitorSummary:
    stats: Stats = field(default_factory=Stats)
    recent: list[RecentCheck] = field(default_factory=list)  # oldest → newest


def _f(value: object) -> float | None:
    return None if value is None else round(float(value), 2)  # type: ignore[arg-type]


async def stats_for(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Stats]:
    if not ids:
        return {}
    rows = (await session.execute(STATS_SQL, {"ids": ids})).mappings().all()
    return {
        r["monitor_id"]: Stats(
            uptime_24h=_f(r["uptime_24h"]),
            uptime_7d=_f(r["uptime_7d"]),
            uptime_30d=_f(r["uptime_30d"]),
            avg_response_ms_24h=_f(r["avg_response_ms_24h"]),
            p95_response_ms_24h=_f(r["p95_response_ms_24h"]),
            checks_24h=int(r["checks_24h"] or 0),
        )
        for r in rows
    }


async def summaries_for(
    session: AsyncSession, ids: list[uuid.UUID], recent: int = 90
) -> dict[uuid.UUID, MonitorSummary]:
    out: dict[uuid.UUID, MonitorSummary] = defaultdict(MonitorSummary)
    if not ids:
        return out
    for mid, stats in (await stats_for(session, ids)).items():
        out[mid].stats = stats
    rows = (await session.execute(RECENT_SQL, {"ids": ids, "n": recent})).mappings().all()
    for r in rows:
        out[r["monitor_id"]].recent.append(
            RecentCheck(
                r["success"],
                r["checked_at"],
                r["response_time_ms"],
                r["status_code"],
                r["error_type"],
            )
        )
    for summary in out.values():
        summary.recent.sort(key=lambda c: c.checked_at)
    return out


async def latency_24h(session: AsyncSession, monitor_id: uuid.UUID) -> list[RecentCheck]:
    rows = (await session.execute(LATENCY_24H_SQL, {"id": monitor_id})).all()
    return [RecentCheck(r.success, r.checked_at, r.response_time_ms, None, None) for r in rows]


async def newest_result_at(session: AsyncSession) -> datetime | None:
    return await session.scalar(NEWEST_RESULT_SQL)
