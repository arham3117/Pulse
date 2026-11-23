"""/api/v1/incidents."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from pulse.api.deps import IsAdmin, SessionDep
from pulse.api.schemas import IncidentOut
from pulse.core.models import Incident, Monitor

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    session: SessionDep,
    is_admin: IsAdmin,
    open: Annotated[
        bool | None, Query(description="true = only open, false = only resolved")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Incident]:
    stmt = (
        select(Incident)
        .join(Monitor, Monitor.id == Incident.monitor_id)
        .order_by(Incident.started_at.desc())
        .limit(limit)
    )
    if not is_admin:
        stmt = stmt.where(Monitor.is_public)
    if open is True:
        stmt = stmt.where(Incident.resolved_at.is_(None))
    elif open is False:
        stmt = stmt.where(Incident.resolved_at.is_not(None))
    return list((await session.scalars(stmt)).all())
