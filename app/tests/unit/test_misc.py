"""Config, logging redaction, scheduler math, notifier formatting, status-page helpers."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from pydantic import ValidationError

from pulse.api.queries import RecentCheck
from pulse.api.routes.pages import is_stale, latency_svg_points, overall_banner, tls_days_left
from pulse.core.config import Settings
from pulse.core.logging import redact_url, redact_urls_processor
from pulse.core.notifier import (
    DiscordWebhookNotifier,
    EventKind,
    LogNotifier,
    NotificationEvent,
    SlackWebhookNotifier,
    build_notifier,
    format_duration,
    format_message,
)
from pulse.core.queue import CheckMessage
from pulse.scheduler.main import compute_next_check

NOW = datetime(2026, 3, 1, 14, 2, tzinfo=UTC)

# --- config -----------------------------------------------------------------------------


def test_settings_defaults(monkeypatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("PULSE_"):
            monkeypatch.delenv(key)
    s = Settings()
    assert s.worker_concurrency == 50
    assert s.scheduler_interval_seconds == 5
    assert s.scheduler_batch_size == 500
    assert s.retention_days == 30
    assert s.chaos_break_readiness is False
    assert s.db_connect_args == {}


def test_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_ALLOW_PRIVATE_HOSTS", "pulse-target, Other ,")
    monkeypatch.setenv("PULSE_DB_SSLMODE", "require")
    monkeypatch.setenv("PULSE_SQS_ENDPOINT_URL", "")
    monkeypatch.setenv("PULSE_CHAOS_BREAK_READINESS", "true")
    s = Settings()
    assert s.allow_private_hosts == ["pulse-target", "other"]
    assert s.db_connect_args == {"ssl": "require"}
    assert s.sqs_endpoint_url is None
    assert s.chaos_break_readiness is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PULSE_WORKER_CONCURRENCY", "0"),
        ("PULSE_DB_PORT", "70000"),
        ("PULSE_ENV", "staging"),
        ("PULSE_DB_SSLMODE", "sometimes"),
        ("PULSE_LOG_LEVEL", "LOUD"),
        ("PULSE_RETENTION_DAYS", "0"),
    ],
)
def test_settings_validation(monkeypatch, key, value) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings()


def test_db_url_escapes_password_and_hides_it() -> None:
    s = Settings(
        db_password="p@ss/w:rd#1", db_user="pulse", db_host="db", db_name="pulse", db_port=5432
    )
    url = s.db_url
    assert url.password == "p@ss/w:rd#1"
    assert "p@ss" not in repr(s)
    assert (
        url.render_as_string(hide_password=True) == "postgresql+asyncpg://pulse:***@db:5432/pulse"
    )


# --- logging ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/x?token=abc", "https://example.com/x?<redacted>"),
        ("https://user:pw@example.com:8443/a#frag", "https://example.com:8443/a"),
        ("http://example.com/plain", "http://example.com/plain"),
    ],
)
def test_redact_url(url, expected) -> None:
    assert redact_url(url) == expected


def test_log_processor_redacts_urls_inside_strings() -> None:
    out = redact_urls_processor(
        None, "info", {"event": "x", "msg": "GET https://h/p?key=s3cret failed"}
    )
    assert "s3cret" not in out["msg"]
    assert out["msg"] == "GET https://h/p?<redacted> failed"


# --- scheduler math ---------------------------------------------------------------------


def test_next_check_normal_advance() -> None:
    prev = NOW - timedelta(seconds=2)
    assert compute_next_check(prev, 60, NOW) == prev + timedelta(seconds=60)


def test_next_check_no_catch_up_burst_after_downtime() -> None:
    prev = NOW - timedelta(hours=1)
    assert compute_next_check(prev, 60, NOW) == NOW + timedelta(seconds=60)


def test_next_check_exact_boundary() -> None:
    prev = NOW - timedelta(seconds=60)
    assert compute_next_check(prev, 60, NOW) == NOW + timedelta(seconds=60)


def test_check_message_roundtrip_and_poison() -> None:
    import uuid

    msg = CheckMessage(uuid.uuid4(), NOW)
    assert CheckMessage.from_body(msg.to_body()) == msg
    for bad in (
        "not json",
        "{}",
        '{"monitor_id":"x","scheduled_for":"2026-01-01T00:00:00+00:00"}',
        '{"monitor_id":"' + str(uuid.uuid4()) + '","scheduled_for":"2026-01-01T00:00:00"}',
    ):
        with pytest.raises((ValueError, KeyError)):
            CheckMessage.from_body(bad)


# --- notifier ---------------------------------------------------------------------------


def test_format_messages_match_plan_examples() -> None:
    opened = NotificationEvent(
        EventKind.INCIDENT_OPENED,
        "Marketing site",
        "https://example.com",
        NOW,
        error="timeout after 5000 ms",
        consecutive_failures=3,
        started_at=NOW,
    )
    assert format_message(opened) == (
        "🔴 DOWN: Marketing site (https://example.com) — timeout after 5000 ms. "
        "3 consecutive failures since 14:02 UTC."
    )
    resolved = NotificationEvent(
        EventKind.INCIDENT_RESOLVED,
        "Marketing site",
        "https://example.com",
        NOW,
        downtime=timedelta(minutes=7, seconds=12),
    )
    assert format_message(resolved) == "✅ RECOVERED: Marketing site — down for 7m 12s."
    tls = NotificationEvent(
        EventKind.TLS_EXPIRING,
        "Site",
        "https://example.com/?k=secret",
        NOW,
        tls_expires_at=NOW + timedelta(days=10),
    )
    text = format_message(tls)
    assert "in 10 days" in text and "secret" not in text


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=5), "0m 5s"),
        (timedelta(hours=2, minutes=3, seconds=4), "2h 3m 4s"),
        (timedelta(days=1, hours=1, minutes=1), "1d 1h 1m"),
    ],
)
def test_format_duration(delta, expected) -> None:
    assert format_duration(delta) == expected


def test_build_notifier_by_host() -> None:
    assert isinstance(build_notifier(None), LogNotifier)
    assert isinstance(build_notifier(""), LogNotifier)
    assert isinstance(
        build_notifier("https://hooks.slack.com/services/T/B/X"), SlackWebhookNotifier
    )
    assert isinstance(
        build_notifier("https://discord.com/api/webhooks/1/abc"), DiscordWebhookNotifier
    )
    with pytest.raises(ValueError):
        build_notifier("https://evil.example/hook")


@respx.mock
async def test_webhook_retries_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("pulse.core.notifier.asyncio.sleep", _no_sleep)
    route = respx.post("https://hooks.slack.com/services/T/B/X").mock(
        side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200)]
    )
    notifier = SlackWebhookNotifier("https://hooks.slack.com/services/T/B/X")
    event = NotificationEvent(
        EventKind.INCIDENT_RESOLVED, "m", "https://x", NOW, downtime=timedelta(1)
    )
    assert await notifier.notify(event) is True
    assert route.call_count == 3
    assert b"RECOVERED" in route.calls.last.request.read()
    await notifier.aclose()


@respx.mock
async def test_webhook_gives_up_after_three(monkeypatch) -> None:
    monkeypatch.setattr("pulse.core.notifier.asyncio.sleep", _no_sleep)
    route = respx.post("https://discord.com/api/webhooks/1/abc").respond(500)
    notifier = DiscordWebhookNotifier("https://discord.com/api/webhooks/1/abc")
    event = NotificationEvent(
        EventKind.INCIDENT_RESOLVED, "m", "https://x", NOW, downtime=timedelta(1)
    )
    assert await notifier.notify(event) is False
    assert route.call_count == 3
    await notifier.aclose()


async def _no_sleep(_: float) -> None:
    return None


# --- status page helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "level"),
    [
        ([], "unknown"),
        (["unknown", "paused"], "unknown"),
        (["up", "up"], "ok"),
        (["up", "up", "up", "down"], "partial"),
        (["up", "down"], "major"),
        (["down"], "major"),
    ],
)
def test_overall_banner(statuses, level) -> None:
    assert overall_banner(statuses).level == level


def test_stale_banner_logic() -> None:
    assert is_stale(None, True, NOW)
    assert is_stale(NOW - timedelta(minutes=4), True, NOW)
    assert not is_stale(NOW - timedelta(minutes=1), True, NOW)
    assert not is_stale(None, False, NOW)


def test_tls_days_and_chart_points() -> None:
    assert tls_days_left(None, NOW) is None
    assert tls_days_left(NOW + timedelta(days=20, hours=1), NOW) == 20
    checks = [
        RecentCheck(True, NOW - timedelta(hours=12), 100, 200, None),
        RecentCheck(True, NOW, 200, 200, None),
        RecentCheck(False, NOW, None, None, "timeout"),
    ]
    points, max_ms = latency_svg_points(checks, now=NOW)
    assert max_ms == 200
    assert points == "400.0,85.0 800.0,10.0"
    assert latency_svg_points([], now=NOW) == ("", 0)


def test_kubernetes_service_link_env_vars_do_not_break_settings(monkeypatch) -> None:
    """Regression (found on kind): Services named pulse-api / pulse-target make Kubernetes
    inject PULSE_API_PORT=tcp://... etc. Settings must ignore them."""
    for svc, port in (("API", 8000), ("TARGET", 8080), ("POSTGRES", 5432), ("ELASTICMQ", 9324)):
        monkeypatch.setenv(f"PULSE_{svc}_PORT", f"tcp://10.96.0.1:{port}")
        monkeypatch.setenv(f"PULSE_{svc}_SERVICE_HOST", "10.96.0.1")
        monkeypatch.setenv(f"PULSE_{svc}_PORT_{port}_TCP", f"tcp://10.96.0.1:{port}")
    s = Settings()
    assert (s.api_listen_port, s.target_listen_port, s.health_listen_port) == (8000, 8080, 9100)
