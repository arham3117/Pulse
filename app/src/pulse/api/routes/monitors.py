"""/api/v1/monitors CRUD, pause/resume, results and stats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select, update

from pulse.api.deps import ApiError, IsAdmin, RequireAdmin, SessionDep, SettingsDep
from pulse.api.queries import stats_for
from pulse.api.schemas import (
    CheckResultOut,
    CheckResultPage,
    MonitorCreate,
    MonitorOut,
    MonitorStats,
    MonitorUpdate,
)
from pulse.core.config import Settings
from pulse.core.logging import get_logger
from pulse.core.models import CheckResult, Incident, Monitor
from pulse.core.url_safety import UnsafeURLError, validate_url

router = APIRouter(prefix="/api/v1/monitors", tags=["monitors"])
log = get_logger()


async def _check_url(url: str, settings: Settings) -> None:
    try:
        await validate_url(url, settings.allow_private_hosts)
    except UnsafeURLError as exc:
        raise ApiError(400, "unsafe_url", str(exc)) from exc


async def _get_visible(session: SessionDep, monitor_id: uuid.UUID, is_admin: bool) -> Monitor:
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None or (not monitor.is_public and not is_admin):
        raise ApiError(404, "not_found", "monitor not found")
    return monitor


@router.post("", status_code=201, response_model=MonitorOut, dependencies=[RequireAdmin])
async def create_monitor(
    body: MonitorCreate, session: SessionDep, settings: SettingsDep
) -> Monitor:
    await _check_url(body.url, settings)
    monitor = Monitor(**body.model_dump())
    session.add(monitor)
    await session.commit()
    await session.refresh(monitor)
    log.info("monitor_created", monitor_id=str(monitor.id))
    return monitor


@router.get("", response_model=list[MonitorOut])
async def list_monitors(session: SessionDep, is_admin: IsAdmin) -> list[Monitor]:
    stmt = select(Monitor).order_by(Monitor.name, Monitor.id)
    if not is_admin:
        stmt = stmt.where(Monitor.is_public)
    return list((await session.scalars(stmt)).all())


@router.get("/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: uuid.UUID, session: SessionDep, is_admin: IsAdmin) -> Monitor:
    return await _get_visible(session, monitor_id, is_admin)


@router.patch("/{monitor_id}", response_model=MonitorOut, dependencies=[RequireAdmin])
async def update_monitor(
    monitor_id: uuid.UUID, body: MonitorUpdate, session: SessionDep, settings: SettingsDep
) -> Monitor:
    monitor = await _get_visible(session, monitor_id, True)
    changes = body.model_dump(exclude_unset=True)
    if "url" in changes:
        await _check_url(changes["url"], settings)
    merged_min = changes.get("expected_status_min", monitor.expected_status_min)
    merged_max = changes.get("expected_status_max", monitor.expected_status_max)
    if merged_min > merged_max:
        raise ApiError(
            422, "validation_error", "expected_status_min must be <= expected_status_max"
        )
    if changes.get("method", monitor.method) == "HEAD" and changes.get("keyword", monitor.keyword):
        raise ApiError(422, "validation_error", "keyword checks need GET, not HEAD")
    if "keyword" in changes and changes["keyword"] == "":
        changes["keyword"] = None
    for key, value in changes.items():
        if value is None and key != "keyword":
            raise ApiError(422, "validation_error", f"{key} cannot be null")
        setattr(monitor, key, value)
    await session.commit()
    await session.refresh(monitor)
    return monitor


@router.delete("/{monitor_id}", status_code=204, dependencies=[RequireAdmin])
async def delete_monitor(monitor_id: uuid.UUID, session: SessionDep) -> Response:
    monitor = await _get_visible(session, monitor_id, True)
    await session.delete(monitor)
    await session.commit()
    log.info("monitor_deleted", monitor_id=str(monitor_id))
    return Response(status_code=204)


@router.post("/{monitor_id}/pause", response_model=MonitorOut, dependencies=[RequireAdmin])
async def pause_monitor(monitor_id: uuid.UUID, session: SessionDep) -> Monitor:
    monitor = await _get_visible(session, monitor_id, True)
    monitor.is_active = False
    monitor.status = "paused"
    # A paused monitor isn't watched any more, so close its open incident (no notification).
    await session.execute(
        update(Incident)
        .where(Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None))
        .values(resolved_at=func.now())
    )
    await session.commit()
    await session.refresh(monitor)
    return monitor


@router.post("/{monitor_id}/resume", response_model=MonitorOut, dependencies=[RequireAdmin])
async def resume_monitor(monitor_id: uuid.UUID, session: SessionDep) -> Monitor:
    monitor = await _get_visible(session, monitor_id, True)
    if not monitor.is_active:
        monitor.is_active = True
        monitor.status = "unknown"
        monitor.consecutive_failures = 0
        monitor.failing_since = None
        monitor.failing_error = None
        monitor.next_check_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(monitor)
    return monitor


@router.get("/{monitor_id}/results", response_model=CheckResultPage)
async def list_results(
    monitor_id: uuid.UUID,
    session: SessionDep,
    is_admin: IsAdmin,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    before: Annotated[
        datetime | None, Query(description="Return results checked before this time")
    ] = None,
) -> CheckResultPage:
    await _get_visible(session, monitor_id, is_admin)
    stmt = (
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor_id)
        .order_by(CheckResult.checked_at.desc())
        .limit(limit)
    )
    if before is not None:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        stmt = stmt.where(CheckResult.checked_at < before)
    rows = list((await session.scalars(stmt)).all())
    next_before = rows[-1].checked_at if len(rows) == limit else None
    return CheckResultPage(
        items=[CheckResultOut.model_validate(r) for r in rows], next_before=next_before
    )


@router.get("/{monitor_id}/stats", response_model=MonitorStats)
async def monitor_stats(
    monitor_id: uuid.UUID, session: SessionDep, is_admin: IsAdmin
) -> MonitorStats:
    await _get_visible(session, monitor_id, is_admin)
    stats = (await stats_for(session, [monitor_id])).get(monitor_id)
    if stats is None:
        return MonitorStats(monitor_id=monitor_id)
    return MonitorStats(monitor_id=monitor_id, **stats.__dict__)
