"""/healthz, /readyz and /metrics for the API process."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from pulse.core.db import db_ready
from pulse.core.metrics import API_REGISTRY, render

router = APIRouter(include_in_schema=False)


@router.get("/healthz")
async def healthz() -> Response:
    """Liveness: the process is up. Deliberately checks nothing else, so a DB outage
    doesn't make Kubernetes restart every API pod."""
    return Response("ok\n", media_type="text/plain")


@router.get("/readyz")
async def readyz(request: Request) -> Response:
    """Readiness: can we serve traffic? Fails → pod removed from the Service/ALB."""
    if request.app.state.settings.chaos_break_readiness:
        return Response("not ready (chaos drill)\n", status_code=503, media_type="text/plain")
    if not await db_ready(request.app.state.engine, timeout=1.0):
        return Response("database unreachable\n", status_code=503, media_type="text/plain")
    return Response("ready\n", media_type="text/plain")


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = render(API_REGISTRY)
    return Response(body, media_type=content_type)
