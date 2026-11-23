import socket
import ssl

import httpx
import pytest
import respx

from pulse.core.url_safety import UnsafeURLError
from pulse.worker.checker import (
    CheckSpec,
    build_http_client,
    classify,
    classify_exception,
    run_check,
)


async def public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.215.14"]


async def private_for_internal(host: str, port: int) -> list[str]:
    return ["10.0.0.7"] if host.startswith("internal") else ["93.184.215.14"]


# --- classify ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "spec", "expected"),
    [
        (200, None, CheckSpec("https://x"), (True, None)),
        (399, None, CheckSpec("https://x"), (True, None)),
        (404, None, CheckSpec("https://x"), (False, "status")),
        (500, None, CheckSpec("https://x"), (False, "status")),
        (
            204,
            None,
            CheckSpec("https://x", expected_status_min=204, expected_status_max=204),
            (True, None),
        ),
        (200, b"hello PULSE_OK", CheckSpec("https://x", keyword="PULSE_OK"), (True, None)),
        (200, b"hello", CheckSpec("https://x", keyword="PULSE_OK"), (False, "keyword")),
        (200, None, CheckSpec("https://x", keyword="PULSE_OK"), (False, "keyword")),
        (503, b"PULSE_OK", CheckSpec("https://x", keyword="PULSE_OK"), (False, "status")),
    ],
)
def test_classify(status, body, spec, expected) -> None:
    ok, err_type, _ = classify(status, body, spec)
    assert (ok, err_type) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectTimeout("t"), "timeout"),
        (httpx.ReadTimeout("t"), "timeout"),
        (TimeoutError(), "timeout"),
        (UnsafeURLError("blocked", "private"), "blocked"),
        (UnsafeURLError("dns", "nx"), "dns"),
        (UnsafeURLError("scheme", "ftp"), "blocked"),
        (httpx.ConnectError("refused"), "connection"),
        (httpx.RemoteProtocolError("bad"), "connection"),
        (ConnectionResetError(), "connection"),
        (ValueError("weird"), "other"),
    ],
)
def test_classify_exception(exc, expected) -> None:
    assert classify_exception(exc, 5000)[0] == expected


def test_classify_exception_walks_cause_chain() -> None:
    dns = httpx.ConnectError("x")
    dns.__cause__ = socket.gaierror(8, "nodename nor servname provided")
    assert classify_exception(dns, 1000)[0] == "dns"
    tls = httpx.ConnectError("x")
    tls.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    assert classify_exception(tls, 1000)[0] == "tls"


def test_timeout_message_mentions_budget() -> None:
    assert classify_exception(httpx.ReadTimeout("t"), 5000)[1] == "timeout after 5000 ms"


# --- run_check --------------------------------------------------------------------------


@pytest.fixture
async def client():
    c = build_http_client(10)
    yield c
    await c.aclose()


@respx.mock
async def test_success(client) -> None:
    respx.get("https://example.com/").respond(200, text="hi")
    out = await run_check(client, CheckSpec("https://example.com/"), [], public_resolver)
    assert out.success and out.status_code == 200 and out.error_type is None
    assert out.response_time_ms is not None and out.response_time_ms >= 0


@respx.mock
async def test_status_failure(client) -> None:
    respx.get("https://example.com/").respond(503)
    out = await run_check(client, CheckSpec("https://example.com/"), [], public_resolver)
    assert not out.success and out.error_type == "status" and out.status_code == 503
    assert out.summary.startswith("HTTP 503")


@respx.mock
async def test_keyword(client) -> None:
    respx.get("https://example.com/").respond(200, text="all good PULSE_OK")
    out = await run_check(
        client, CheckSpec("https://example.com/", keyword="PULSE_OK"), [], public_resolver
    )
    assert out.success
    respx.get("https://example.com/2").respond(200, text="nope")
    out = await run_check(
        client, CheckSpec("https://example.com/2", keyword="PULSE_OK"), [], public_resolver
    )
    assert out.error_type == "keyword"


@respx.mock
async def test_follows_redirects_manually(client) -> None:
    respx.get("https://example.com/a").respond(301, headers={"location": "/b"})
    respx.get("https://example.com/b").respond(302, headers={"location": "https://other.example/c"})
    respx.get("https://other.example/c").respond(200)
    out = await run_check(client, CheckSpec("https://example.com/a"), [], public_resolver)
    assert out.success and out.status_code == 200


@respx.mock
async def test_redirect_to_private_address_is_blocked(client) -> None:
    respx.get("https://example.com/").respond(
        302, headers={"location": "http://internal.svc/admin"}
    )
    route = respx.get("http://internal.svc/admin").respond(200)
    out = await run_check(client, CheckSpec("https://example.com/"), [], private_for_internal)
    assert not out.success and out.error_type == "blocked"
    assert not route.called  # we never connected to the private host


@respx.mock
async def test_redirect_to_metadata_ip_is_blocked(client) -> None:
    respx.get("https://example.com/").respond(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
    )
    out = await run_check(client, CheckSpec("https://example.com/"), [], public_resolver)
    assert out.error_type == "blocked"


@respx.mock
async def test_redirect_limit(client) -> None:
    for i in range(5):
        respx.get(f"https://example.com/{i}").respond(302, headers={"location": f"/{i + 1}"})
    out = await run_check(
        client,
        CheckSpec("https://example.com/0", expected_status_min=200, expected_status_max=299),
        [],
        public_resolver,
    )
    # After 3 hops we stop following and judge the 3xx itself.
    assert out.status_code == 302 and out.error_type == "status"
    assert respx.calls.call_count == 4


@respx.mock
async def test_timeout(client) -> None:
    respx.get("https://example.com/").mock(side_effect=httpx.ReadTimeout("slow"))
    out = await run_check(
        client, CheckSpec("https://example.com/", timeout_ms=1000), [], public_resolver
    )
    assert out.error_type == "timeout" and out.error_message == "timeout after 1000 ms"


@respx.mock
async def test_connection_error(client) -> None:
    respx.get("https://example.com/").mock(side_effect=httpx.ConnectError("refused"))
    out = await run_check(client, CheckSpec("https://example.com/"), [], public_resolver)
    assert out.error_type == "connection"


async def test_blocked_at_check_time(client) -> None:
    async def rebound(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]  # DNS changed since creation (rebinding)

    out = await run_check(client, CheckSpec("https://example.com/"), [], rebound)
    assert out.error_type == "blocked" and out.status_code is None


@respx.mock
async def test_head_method(client) -> None:
    route = respx.head("https://example.com/").respond(200)
    out = await run_check(
        client, CheckSpec("https://example.com/", method="HEAD"), [], public_resolver
    )
    assert out.success and route.called


@respx.mock
async def test_error_message_truncated(client) -> None:
    respx.get("https://example.com/").mock(side_effect=ValueError("x" * 2000))
    out = await run_check(client, CheckSpec("https://example.com/"), [], public_resolver)
    assert out.error_type == "other" and len(out.error_message or "") <= 500
