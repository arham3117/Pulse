"""SSRF protection for user-supplied monitor URLs (ADR 0007).

Workers fetch arbitrary URLs from inside the VPC. Without this module a user could make
Pulse request ``http://169.254.169.254/`` (instance metadata), the Kubernetes API, RDS, or
any other private service.

``validate_url`` is called twice:
  * at monitor creation/update (API answers 400), and
  * again at check time, because DNS can change between the two (DNS rebinding).

Every redirect hop is validated again by the worker (``follow_redirects=False`` + manual
loop). Residual risk: there is still a small window between our DNS lookup and httpx's own
lookup when it connects; see ADR 0007 for mitigations (IP pinning or an egress proxy).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_URL_LENGTH = 2048

# Explicit block list. ``ipaddress``'s is_private/is_reserved flags already cover most of
# these, but listing them keeps the policy readable and testable.
BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # "this network" / unspecified
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, includes 169.254.169.254 metadata
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved (includes 255.255.255.255 broadcast)
        "::/128",  # unspecified
        "::1/128",  # loopback
        "::ffff:0:0/96",  # IPv4-mapped (checked again after unmapping)
        "64:ff9b::/96",  # NAT64 (can embed private IPv4)
        "100::/64",  # discard
        "2001:db8::/32",  # documentation
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
        "fd00:ec2::254/128",  # AWS IMDS over IPv6 (already inside fc00::/7)
    )
)


class UnsafeURLError(ValueError):
    """The URL is not allowed. ``reason`` is a short machine-readable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]
    exempt: bool = False


def is_blocked_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return is_blocked_ip(ip.ipv4_mapped)
    if any(ip in net for net in BLOCKED_NETWORKS):
        return True
    # Belt and braces for anything the table missed.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def parse_url(url: str) -> tuple[str, str, int]:
    """Syntactic checks only. Returns (scheme, lowercase host, port)."""
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeURLError("too_long", f"URL longer than {MAX_URL_LENGTH} characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("invalid", f"invalid URL: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError("scheme", "only http and https URLs are allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("invalid", "URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("userinfo", "credentials in URLs are not allowed")
    return scheme, host, port or (443 if scheme == "https" else 80)


async def validate_url(
    url: str,
    allow_private_hosts: Iterable[str] = (),
    resolver: Resolver = system_resolver,
) -> ValidatedTarget:
    """Raise ``UnsafeURLError`` unless every address ``url``'s host resolves to is public.

    Hostnames in ``allow_private_hosts`` (only the in-cluster chaos target) skip the IP check.
    """
    scheme, host, port = parse_url(url)
    exempt = {h.strip().lower() for h in allow_private_hosts if h.strip()}
    if host in exempt:
        return ValidatedTarget(url, scheme, host, port, (), exempt=True)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolver(host, port)
        except (OSError, UnicodeError) as exc:
            raise UnsafeURLError("dns", f"could not resolve host {host!r}") from exc
        if not addresses:
            raise UnsafeURLError("dns", f"host {host!r} has no addresses")

    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # strip IPv6 zone id
        if is_blocked_ip(ip):
            raise UnsafeURLError(
                "blocked", f"host {host!r} resolves to a non-public address ({ip})"
            )
    return ValidatedTarget(url, scheme, host, port, tuple(addresses))
