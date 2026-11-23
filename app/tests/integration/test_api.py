import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import insert

from pulse.api.main import create_app
from pulse.core.models import CheckResult, Incident
from tests.conftest import ADMIN_KEY

pytestmark = pytest.mark.integration
AUTH = {"X-API-Key": ADMIN_KEY}


@pytest.fixture
async def api(engine, settings):
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://pulse"
        ) as client,
    ):
        yield client


async def _create(api, **body):
    payload = {"name": "Local target", "url": "http://localhost:8080/ok", **body}
    resp = await api.post("/api/v1/monitors", json=payload, headers=AUTH)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_health_endpoints(api, settings) -> None:
    assert (await api.get("/healthz")).status_code == 200
    assert (await api.get("/readyz")).status_code == 200
    metrics = (await api.get("/metrics")).text
    assert "pulse_http_requests_total" in metrics


async def test_readyz_chaos_flag(engine, settings) -> None:
    broken = settings.model_copy(update={"chaos_break_readiness": True})
    app = create_app(broken)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c,
    ):
        assert (await c.get("/readyz")).status_code == 503
        assert (await c.get("/healthz")).status_code == 200


async def test_readyz_fails_when_db_unreachable(database, settings) -> None:
    app = create_app(settings.model_copy(update={"db_port": 1}))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c,
    ):
        assert (await c.get("/readyz")).status_code == 503


async def test_auth_required_for_writes(api) -> None:
    body = {"name": "x", "url": "https://example.com"}
    for headers in ({}, {"X-API-Key": "wrong"}):
        resp = await api.post("/api/v1/monitors", json=body, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


async def test_crud_roundtrip(api) -> None:
    m = await _create(api, interval_seconds=30, keyword="PULSE_OK")
    assert m["status"] == "unknown" and m["is_active"] and m["method"] == "GET"

    listed = (await api.get("/api/v1/monitors")).json()
    assert [x["id"] for x in listed] == [m["id"]]

    resp = await api.patch(f"/api/v1/monitors/{m['id']}", json={"name": "Renamed"}, headers=AUTH)
    assert resp.status_code == 200 and resp.json()["name"] == "Renamed"

    paused = (await api.post(f"/api/v1/monitors/{m['id']}/pause", headers=AUTH)).json()
    assert paused["status"] == "paused" and not paused["is_active"]
    resumed = (await api.post(f"/api/v1/monitors/{m['id']}/resume", headers=AUTH)).json()
    assert resumed["status"] == "unknown" and resumed["is_active"]

    assert (await api.delete(f"/api/v1/monitors/{m['id']}", headers=AUTH)).status_code == 204
    resp = await api.get(f"/api/v1/monitors/{m['id']}")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "monitor not found"}}


async def test_private_monitors_hidden_without_key(api) -> None:
    m = await _create(api, is_public=False)
    assert (await api.get("/api/v1/monitors")).json() == []
    assert len((await api.get("/api/v1/monitors", headers=AUTH)).json()) == 1
    assert (await api.get(f"/api/v1/monitors/{m['id']}")).status_code == 404
    assert (await api.get(f"/api/v1/monitors/{m['id']}", headers=AUTH)).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://10.20.0.10/",
        "http://[::1]/",
        "ftp://example.com/",
    ],
)
async def test_ssrf_rejected_at_creation(api, url) -> None:
    resp = await api.post("/api/v1/monitors", json={"name": "x", "url": url}, headers=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsafe_url"


async def test_ssrf_rejected_on_update(api) -> None:
    m = await _create(api)
    resp = await api.patch(
        f"/api/v1/monitors/{m['id']}", json={"url": "http://192.168.0.1/"}, headers=AUTH
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "url": "http://localhost/"},
        {"name": "x" * 101, "url": "http://localhost/"},
        {"name": "x", "url": "http://localhost/", "interval_seconds": 10},
        {"name": "x", "url": "http://localhost/", "timeout_ms": 50000},
        {"name": "x", "url": "http://localhost/", "method": "POST"},
        {"name": "x", "url": "http://localhost/", "failure_threshold": 0},
        {
            "name": "x",
            "url": "http://localhost/",
            "expected_status_min": 500,
            "expected_status_max": 200,
        },
        {"name": "x", "url": "http://localhost/", "method": "HEAD", "keyword": "ok"},
        {"name": "x", "url": "http://localhost/", "unknown_field": 1},
    ],
)
async def test_validation_errors(api, body) -> None:
    resp = await api.post("/api/v1/monitors", json=body, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_results_pagination_stats_and_incidents(api, sessionmaker) -> None:
    m = await _create(api)
    mid = uuid.UUID(m["id"])
    now = datetime.now(UTC)
    rows = [
        {
            "monitor_id": mid,
            "scheduled_for": now - timedelta(minutes=i),
            "checked_at": now - timedelta(minutes=i),
            "success": i % 4 != 0,
            "status_code": 200 if i % 4 else 503,
            "response_time_ms": 100 + i,
            "error_type": None if i % 4 else "status",
            "worker_id": "t",
        }
        for i in range(1, 9)
    ]
    async with sessionmaker() as s, s.begin():
        await s.execute(insert(CheckResult), rows)
        s.add(Incident(monitor_id=mid, started_at=now - timedelta(hours=1), first_error="HTTP 503"))

    page1 = (await api.get(f"/api/v1/monitors/{mid}/results", params={"limit": 5})).json()
    assert len(page1["items"]) == 5 and page1["next_before"]
    page2 = (
        await api.get(
            f"/api/v1/monitors/{mid}/results", params={"limit": 5, "before": page1["next_before"]}
        )
    ).json()
    assert len(page2["items"]) == 3 and page2["next_before"] is None
    all_ids = [r["id"] for r in page1["items"] + page2["items"]]
    assert len(set(all_ids)) == 8

    stats = (await api.get(f"/api/v1/monitors/{mid}/stats")).json()
    assert stats["checks_24h"] == 8
    assert stats["uptime_24h"] == 75.0  # 2 of 8 failed
    assert stats["p95_response_ms_24h"] is not None

    open_incidents = (await api.get("/api/v1/incidents", params={"open": "true"})).json()
    assert len(open_incidents) == 1 and open_incidents[0]["first_error"] == "HTTP 503"
    assert (await api.get("/api/v1/incidents", params={"open": "false"})).json() == []

    # Pausing closes the open incident.
    await api.post(f"/api/v1/monitors/{mid}/pause", headers=AUTH)
    assert (await api.get("/api/v1/incidents", params={"open": "true"})).json() == []


async def test_status_pages_render(api, sessionmaker) -> None:
    empty = await api.get("/")
    assert empty.status_code == 200 and "No public monitors yet" in empty.text
    m = await _create(api, name="Marketing site")
    async with sessionmaker() as s, s.begin():
        now = datetime.now(UTC)
        await s.execute(
            insert(CheckResult),
            [
                {
                    "monitor_id": uuid.UUID(m["id"]),
                    "scheduled_for": now,
                    "checked_at": now,
                    "success": True,
                    "status_code": 200,
                    "response_time_ms": 42,
                    "worker_id": "t",
                }
            ],
        )
    page = await api.get("/")
    assert "Marketing site" in page.text and "42 ms" in page.text
    assert "Data may be stale" not in page.text
    detail = await api.get(f"/monitors/{m['id']}")
    assert detail.status_code == 200 and "<polyline" in detail.text
    assert (await api.get(f"/monitors/{uuid.uuid4()}")).status_code == 404


async def test_metrics_use_route_template(api) -> None:
    await api.get(f"/api/v1/monitors/{uuid.uuid4()}")
    await api.get("/no/such/path")
    metrics = (await api.get("/metrics")).text
    assert 'route="/api/v1/monitors/{monitor_id}"' in metrics
    assert 'route="unmatched"' in metrics
    assert "/no/such/path" not in metrics


async def test_request_id_header(api) -> None:
    resp = await api.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"
    assert len((await api.get("/healthz")).headers["x-request-id"]) == 32
