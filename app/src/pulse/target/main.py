"""pulse-target: an in-cluster "chaos target" for demos, alert tests and load tests.

Never exposed publicly (ClusterIP only). The /switch state lives in process memory, so it
resets on restart and differs between replicas; run exactly one replica. That's fine for a
demo target and keeps it dependency-free.
"""

from __future__ import annotations

import asyncio
import random

import uvicorn
from fastapi import FastAPI, Query, Response
from fastapi.responses import PlainTextResponse

from pulse.core.config import get_settings
from pulse.core.logging import configure_logging

MAX_SLOW_MS = 30_000


def create_app() -> FastAPI:
    app = FastAPI(title="pulse-target", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.switch_up = True

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/readyz", response_class=PlainTextResponse)
    async def readyz() -> str:
        return "ready"

    @app.get("/ok", response_class=PlainTextResponse)
    async def ok() -> str:
        return "PULSE_OK"

    @app.get("/slow", response_class=PlainTextResponse)
    async def slow(ms: int = Query(2000, ge=0)) -> str:
        await asyncio.sleep(min(ms, MAX_SLOW_MS) / 1000)
        return "PULSE_OK"

    @app.get("/flaky")
    async def flaky(fail_rate: float = Query(0.3, ge=0.0, le=1.0)) -> Response:
        if random.random() < fail_rate:
            return PlainTextResponse("flaky failure", status_code=500)
        return PlainTextResponse("PULSE_OK")

    @app.get("/status/{code}")
    async def status(code: int) -> Response:
        if not 100 <= code <= 599:
            return PlainTextResponse("status must be 100-599", status_code=400)
        return Response(status_code=code)

    @app.get("/switch")
    async def switch() -> Response:
        if app.state.switch_up:
            return PlainTextResponse("PULSE_OK")
        return PlainTextResponse("switched down", status_code=503)

    @app.post("/switch/up", response_class=PlainTextResponse)
    async def switch_up() -> str:
        app.state.switch_up = True
        return "up"

    @app.post("/switch/down", response_class=PlainTextResponse)
    async def switch_down() -> str:
        app.state.switch_up = False
        return "down"

    return app


def run() -> None:
    settings = get_settings()
    configure_logging("target", settings.log_level)
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # noqa: S104 - container port
        port=settings.target_listen_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    run()
