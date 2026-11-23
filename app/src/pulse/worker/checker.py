"""The HTTP check itself: SSRF validation, manual redirects, classification, TLS expiry.

Nothing in here touches the database or the queue, so it can be tested with ``respx`` and
a fake resolver.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from pulse.core.url_safety import Resolver, UnsafeURLError, system_resolver, validate_url

MAX_REDIRECTS = 3
MAX_BODY_BYTES = 1024 * 1024  # read at most 1 MB when looking for a keyword
MAX_ERROR_LEN = 500
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
USER_AGENT = "PulseMonitor/1.0 (+https://github.com/YOUR_GITHUB_USER/pulse)"


@dataclass(frozen=True, slots=True)
class CheckSpec:
    url: str
    method: str = "GET"
    timeout_ms: int = 5000
    expected_status_min: int = 200
    expected_status_max: int = 399
    keyword: str | None = None


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    success: bool
    status_code: int | None
    response_time_ms: int | None
    error_type: str | None
    error_message: str | None

    @property
    def summary(self) -> str:
        """Short reason for notifications, e.g. ``timeout after 5000 ms`` or ``HTTP 503``."""
        if self.success:
            return "ok"
        return self.error_message or self.error_type or "failed"


def build_http_client(concurrency: int) -> httpx.AsyncClient:
    """One shared client per worker process (connection pooling across checks)."""
    return httpx.AsyncClient(
        follow_redirects=False,  # we follow manually so every hop is SSRF-validated
        headers={"User-Agent": USER_AGENT},
        limits=httpx.Limits(
            max_connections=concurrency * 2,
            max_keepalive_connections=concurrency,
            keepalive_expiry=30,
        ),
        trust_env=False,  # never pick up proxy env vars by accident
    )


def classify(
    status_code: int, body: bytes | None, spec: CheckSpec
) -> tuple[bool, str | None, str | None]:
    """Decide success from status code and keyword. Returns (success, error_type, message)."""
    if not spec.expected_status_min <= status_code <= spec.expected_status_max:
        return (
            False,
            "status",
            f"HTTP {status_code} (expected {spec.expected_status_min}-{spec.expected_status_max})",
        )
    if spec.keyword and (body is None or spec.keyword.encode() not in body):
        return False, "keyword", f"keyword {spec.keyword!r} not found in response body"
    return True, None, None


def classify_exception(exc: BaseException, timeout_ms: int) -> tuple[str, str]:
    """Map an exception to (error_type, message)."""
    if isinstance(exc, UnsafeURLError):
        return ("dns" if exc.reason == "dns" else "blocked"), str(exc)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout", f"timeout after {timeout_ms} ms"
    chain = _exception_chain(exc)
    if any(isinstance(e, socket.gaierror) for e in chain):
        return "dns", "DNS resolution failed"
    if any(isinstance(e, ssl.SSLError | ssl.CertificateError) for e in chain):
        return "tls", f"TLS error: {_short(chain[-1])}"
    if isinstance(exc, httpx.ConnectError) and "ssl" in str(exc).lower():
        return "tls", f"TLS error: {_short(exc)}"
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
        return "connection", f"connection error: {_short(exc)}"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection", f"connection error: {_short(exc)}"
    return "other", f"{type(exc).__name__}: {_short(exc)}"


async def run_check(
    client: httpx.AsyncClient,
    spec: CheckSpec,
    allow_private_hosts: list[str],
    resolver: Resolver = system_resolver,
) -> CheckOutcome:
    """Run one check. Never raises (except cancellation)."""
    timeout_s = spec.timeout_ms / 1000
    start = time.monotonic()
    status_code: int | None = None
    try:
        async with asyncio.timeout(timeout_s):
            url = spec.url
            for _hop in range(MAX_REDIRECTS + 1):
                await validate_url(url, allow_private_hosts, resolver)
                request = client.build_request(spec.method, url, timeout=timeout_s)
                response = await client.send(request, stream=True)
                try:
                    status_code = response.status_code
                    location = response.headers.get("location")
                    if status_code in REDIRECT_CODES and location and _hop < MAX_REDIRECTS:
                        url = str(response.url.join(location))
                        continue
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    body = await _read_limited(response) if spec.keyword else None
                finally:
                    await response.aclose()
                ok, err_type, err_msg = classify(status_code, body, spec)
                return CheckOutcome(ok, status_code, elapsed_ms, err_type, _trim(err_msg))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        err_type, err_msg = classify_exception(exc, spec.timeout_ms)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckOutcome(False, status_code, elapsed_ms, err_type, _trim(err_msg))
    raise AssertionError("unreachable")  # pragma: no cover


async def fetch_tls_expiry(host: str, port: int = 443, timeout: float = 5.0) -> datetime | None:
    """Open a verified TLS connection and return the leaf certificate's notAfter (UTC)."""
    ctx = ssl.create_default_context()
    try:
        async with asyncio.timeout(timeout):
            _reader, writer = await asyncio.open_connection(
                host, port, ssl=ctx, server_hostname=host
            )
        try:
            ssl_obj = writer.get_extra_info("ssl_object")
            cert = ssl_obj.getpeercert() if ssl_obj else None
        finally:
            writer.close()
    except (OSError, TimeoutError, ssl.SSLError):
        return None
    not_after = cert.get("notAfter") if cert else None
    if not isinstance(not_after, str):
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=UTC)


async def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES]


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _short(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text.splitlines()[0][:200]


def _trim(message: str | None) -> str | None:
    return message[:MAX_ERROR_LEN] if message else message
