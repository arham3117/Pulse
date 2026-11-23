"""Server-rendered status page (Jinja2, no JS framework)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from pulse.api.deps import ApiError, SessionDep
from pulse.api.queries import (
    MonitorSummary,
    RecentCheck,
    latency_24h,
    newest_result_at,
    summaries_for,
)
from pulse.core.models import Incident, Monitor

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

STALE_AFTER = timedelta(minutes=3)


@dataclass
class Banner:
    level: str  # ok | partial | major | unknown
    text: str


def overall_banner(statuses: list[str]) -> Banner:
    watched = [s for s in statuses if s in ("up", "down")]
    if not watched:
        return Banner("unknown", "No data yet")
    down = watched.count("down")
    if down == 0:
        return Banner("ok", "All systems operational")
    if down * 2 >= len(watched):
        return Banner("major", "Major outage")
    return Banner("partial", "Partial outage")


def is_stale(newest: datetime | None, has_active: bool, now: datetime) -> bool:
    return has_active and (newest is None or now - newest > STALE_AFTER)


def tls_days_left(expires: datetime | None, now: datetime) -> int | None:
    return None if expires is None else (expires - now).days


def latency_svg_points(
    checks: list[RecentCheck],
    width: int = 800,
    height: int = 160,
    window: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> tuple[str, int]:
    """Polyline points for an inline SVG latency chart. Returns (points, max_ms)."""
    now = now or datetime.now(UTC)
    pts = [c for c in checks if c.response_time_ms is not None]
    if not pts:
        return "", 0
    max_ms = max(max(c.response_time_ms or 0 for c in pts), 1)
    start = now - window
    coords = []
    for c in pts:
        x = (c.checked_at - start).total_seconds() / window.total_seconds() * width
        y = height - (c.response_time_ms or 0) / max_ms * (height - 10)
        coords.append(f"{max(0.0, x):.1f},{y:.1f}")
    return " ".join(coords), max_ms


@router.get("/", response_class=HTMLResponse)
async def status_page(request: Request, session: SessionDep) -> HTMLResponse:
    now = datetime.now(UTC)
    monitors = list(
        (
            await session.scalars(select(Monitor).where(Monitor.is_public).order_by(Monitor.name))
        ).all()
    )
    summaries = await summaries_for(session, [m.id for m in monitors])
    newest = await newest_result_at(session)
    rows = [
        {
            "m": m,
            "s": summaries.get(m.id, MonitorSummary()),
            "tls_days": tls_days_left(m.tls_expires_at, now),
        }
        for m in monitors
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows,
            "banner": overall_banner([m.status for m in monitors]),
            "stale": is_stale(newest, any(m.is_active for m in monitors), now),
            "newest": newest,
            "now": now,
        },
    )


@router.get("/monitors/{monitor_id}", response_class=HTMLResponse)
async def monitor_page(
    monitor_id: uuid.UUID, request: Request, session: SessionDep
) -> HTMLResponse:
    now = datetime.now(UTC)
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None or not monitor.is_public:
        raise ApiError(404, "not_found", "monitor not found")
    summary = (await summaries_for(session, [monitor.id])).get(monitor.id, MonitorSummary())
    checks = await latency_24h(session, monitor.id)
    points, max_ms = latency_svg_points(checks, now=now)
    incidents = list(
        (
            await session.scalars(
                select(Incident)
                .where(Incident.monitor_id == monitor.id)
                .order_by(Incident.started_at.desc())
                .limit(50)
            )
        ).all()
    )
    newest = await newest_result_at(session)
    return templates.TemplateResponse(
        request,
        "monitor.html",
        {
            "m": monitor,
            "s": summary,
            "points": points,
            "max_ms": max_ms,
            "incidents": incidents,
            "tls_days": tls_days_left(monitor.tls_expires_at, now),
            "stale": is_stale(newest, monitor.is_active, now),
            "now": now,
        },
    )
