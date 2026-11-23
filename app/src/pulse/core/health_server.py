"""Minimal asyncio HTTP server for ``/healthz``, ``/readyz`` and ``/metrics`` on :9100.

Used by the scheduler and worker, which have no web framework of their own. It only
understands ``GET <path>``; anything else gets 404/405. Deliberately tiny: no
dependencies, no keep-alive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from prometheus_client import CollectorRegistry

from pulse.core.metrics import render

ReadyCheck = Callable[[], Awaitable[bool]]


class HealthServer:
    def __init__(
        self,
        registry: CollectorRegistry,
        ready_check: ReadyCheck,
        host: str = "0.0.0.0",  # noqa: S104 - must be reachable by kubelet/Prometheus
        port: int = 9100,
    ) -> None:
        self._registry = registry
        self._ready_check = ready_check
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(5):
                request_line = await reader.readline()
                # Drain headers; we don't use them.
                while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                    del line
            parts = request_line.decode("latin-1").split()
            method, path = (parts[0], parts[1].split("?", 1)[0]) if len(parts) >= 2 else ("", "")
            status, ctype, body = await self._route(method, path)
        except Exception:
            status, ctype, body = 500, "text/plain", b"error\n"
        head = (
            f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        )
        try:
            writer.write(head.encode("latin-1") + body)
            await writer.drain()
        finally:
            writer.close()

    async def _route(self, method: str, path: str) -> tuple[int, str, bytes]:
        if method != "GET":
            return 405, "text/plain", b"method not allowed\n"
        if path == "/healthz":
            return 200, "text/plain", b"ok\n"
        if path == "/readyz":
            ok = await self._ready_check()
            return (200, "text/plain", b"ready\n") if ok else (503, "text/plain", b"not ready\n")
        if path == "/metrics":
            body, ctype = render(self._registry)
            return 200, ctype, body
        return 404, "text/plain", b"not found\n"


_REASONS = {
    200: "OK",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    503: "Service Unavailable",
}
