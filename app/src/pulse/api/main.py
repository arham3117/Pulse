"""FastAPI application: JSON API, status page, health and metrics."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pulse.api.deps import ApiError
from pulse.api.routes import health, incidents, monitors, pages
from pulse.core.config import Settings, get_settings
from pulse.core.db import create_engine, create_sessionmaker
from pulse.core.logging import configure_logging, get_logger
from pulse.core.metrics import HTTP_DURATION, HTTP_REQUESTS

log = get_logger()

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


def _error(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


class RequestContextMiddleware:
    """Request ID, JSON access log and RED metrics, as a pure ASGI middleware.

    The ``route`` label is the matched route *template* (``/api/v1/monitors/{monitor_id}``),
    read from ``scope["route"]`` after routing, so metrics cardinality stays bounded.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_id = headers.get(b"x-request-id", b"").decode("latin-1")[:128]
        request_id = raw_id if raw_id and raw_id.isprintable() else uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
            HTTP_DURATION.labels(method=method, route=route).observe(duration)
            if route not in ("/metrics", "/healthz", "/readyz"):  # probes would flood the logs
                log.info(
                    "http_request",
                    method=method,
                    route=route,
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round(duration * 1000, 1),
                )
            structlog.contextvars.unbind_contextvars("request_id")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        log.info("api_started", env=settings.env)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Pulse",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = first.get("msg", "invalid request")
        return _error(422, "validation_error", f"{loc}: {msg}" if loc else msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail), getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=type(exc).__name__)
        return _error(500, "internal_error", "internal server error")

    app.add_middleware(RequestContextMiddleware)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )
    app.include_router(health.router)
    app.include_router(monitors.router)
    app.include_router(incidents.router)
    app.include_router(pages.router)
    return app


def run() -> None:
    settings = get_settings()
    configure_logging("api", settings.log_level)
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - container port
        port=settings.api_listen_port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",  # behind the ALB; only used for client IP in logs
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    run()
