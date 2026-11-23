"""Health server and chaos target (no database needed)."""

import asyncio

import httpx
import pytest

from pulse.core.health_server import HealthServer
from pulse.core.metrics import WORKER_INFLIGHT, WORKER_REGISTRY
from pulse.target.main import create_app


async def _raw_get(port: int, path: str, method: str = "GET") -> tuple[int, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, body = data.decode().partition("\r\n\r\n")
    return int(head.split()[1]), body


@pytest.mark.parametrize("ready", [True, False])
async def test_health_server(ready: bool) -> None:
    async def check() -> bool:
        return ready

    server = HealthServer(WORKER_REGISTRY, check, host="127.0.0.1", port=0)
    await server.start()
    try:
        WORKER_INFLIGHT.set(0)
        assert await _raw_get(server.port, "/healthz") == (200, "ok\n")
        status, _ = await _raw_get(server.port, "/readyz")
        assert status == (200 if ready else 503)
        status, body = await _raw_get(server.port, "/metrics?x=1")
        assert status == 200
        assert "pulse_worker_inflight_checks 0.0" in body
        assert "pulse_http_requests_total" not in body  # API metrics live in another registry
        assert (await _raw_get(server.port, "/nope"))[0] == 404
        assert (await _raw_get(server.port, "/healthz", "POST"))[0] == 405
    finally:
        await server.stop()


@pytest.fixture
async def target():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://target"
    ) as client:
        yield client


async def test_target_endpoints(target: httpx.AsyncClient) -> None:
    assert (await target.get("/healthz")).status_code == 200
    assert (await target.get("/ok")).text == "PULSE_OK"
    assert (await target.get("/status/418")).status_code == 418
    assert (await target.get("/status/999")).status_code == 400
    assert (await target.get("/slow", params={"ms": 10})).status_code == 200
    assert (await target.get("/flaky", params={"fail_rate": 1})).status_code == 500
    assert (await target.get("/flaky", params={"fail_rate": 0})).status_code == 200
    assert (await target.get("/flaky", params={"fail_rate": 2})).status_code == 422


async def test_target_switch(target: httpx.AsyncClient) -> None:
    assert (await target.get("/switch")).text == "PULSE_OK"
    await target.post("/switch/down")
    assert (await target.get("/switch")).status_code == 503
    await target.post("/switch/up")
    assert (await target.get("/switch")).status_code == 200
