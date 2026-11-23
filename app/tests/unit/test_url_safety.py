import ipaddress

import pytest

from pulse.core.url_safety import UnsafeURLError, is_blocked_ip, parse_url, validate_url


def resolver_for(*addresses: str):
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


async def failing_resolver(host: str, port: int) -> list[str]:
    raise OSError("NXDOMAIN")


BLOCKED = [
    "127.0.0.1",  # loopback
    "127.1.2.3",
    "10.0.0.1",  # private
    "10.20.0.10",  # our VPC
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",  # instance metadata
    "169.254.0.1",  # link-local
    "100.64.0.1",  # carrier-grade NAT
    "100.127.255.254",
    "224.0.0.1",  # multicast
    "239.255.255.250",
    "0.0.0.0",  # unspecified
    "240.0.0.1",  # reserved
    "255.255.255.255",  # broadcast
    "192.0.2.10",  # TEST-NET
    "::1",  # IPv6 loopback
    "::",  # IPv6 unspecified
    "fc00::1",  # unique local
    "fd00:ec2::254",  # AWS IMDS over IPv6
    "fe80::1",  # IPv6 link-local
    "ff02::1",  # IPv6 multicast
    "::ffff:10.0.0.1",  # IPv4-mapped private
    "::ffff:169.254.169.254",
    "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
]

ALLOWED = ["93.184.215.14", "1.1.1.1", "8.8.8.8", "2606:4700:4700::1111", "151.101.1.69"]


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ips(ip: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ALLOWED)
def test_public_ips_allowed(ip: str) -> None:
    assert not is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", BLOCKED)
async def test_hostname_resolving_to_blocked_ip_is_rejected(ip: str) -> None:
    with pytest.raises(UnsafeURLError) as exc:
        await validate_url("https://evil.example/", resolver=resolver_for(ip))
    assert exc.value.reason == "blocked"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
        "http://10.20.0.10/",
        "http://[::1]:8080/",
        "http://0x7f000001/",  # hex-encoded 127.0.0.1 (resolved by getaddrinfo)
    ],
)
async def test_literal_and_local_urls_rejected(url: str) -> None:
    async def local_resolver(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    with pytest.raises(UnsafeURLError):
        await validate_url(url, resolver=local_resolver)


async def test_any_blocked_address_rejects_even_if_others_public() -> None:
    with pytest.raises(UnsafeURLError):
        await validate_url("https://mixed.example/", resolver=resolver_for("1.1.1.1", "10.0.0.5"))


@pytest.mark.parametrize("ip", ALLOWED)
async def test_public_url_allowed(ip: str) -> None:
    target = await validate_url("https://example.com/health?x=1", resolver=resolver_for(ip))
    assert target.host == "example.com"
    assert target.port == 443
    assert target.addresses == (ip,)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://example.com/", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("gopher://example.com/", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("http://", "invalid"),
        ("http://user:pass@example.com/", "userinfo"),
        ("http://example.com:99999/", "invalid"),
        ("http://example.com/" + "a" * 2100, "too_long"),
    ],
)
async def test_syntax_rejections(url: str, reason: str) -> None:
    with pytest.raises(UnsafeURLError) as exc:
        await validate_url(url, resolver=resolver_for("1.1.1.1"))
    assert exc.value.reason == reason


async def test_dns_failure() -> None:
    with pytest.raises(UnsafeURLError) as exc:
        await validate_url("https://nope.invalid/", resolver=failing_resolver)
    assert exc.value.reason == "dns"


async def test_empty_dns_answer() -> None:
    with pytest.raises(UnsafeURLError) as exc:
        await validate_url("https://empty.example/", resolver=resolver_for())
    assert exc.value.reason == "dns"


async def test_allow_private_hosts_exemption_is_exact_hostname() -> None:
    target = await validate_url(
        "http://pulse-target:8080/switch",
        allow_private_hosts=["pulse-target"],
        resolver=resolver_for("10.1.2.3"),
    )
    assert target.exempt
    # Not a suffix/prefix match:
    with pytest.raises(UnsafeURLError):
        await validate_url(
            "http://pulse-target.evil.example/",
            allow_private_hosts=["pulse-target"],
            resolver=resolver_for("10.1.2.3"),
        )


def test_parse_url_defaults_ports_and_lowercases() -> None:
    assert parse_url("HTTP://Example.COM./x") == ("http", "example.com", 80)
    assert parse_url("https://example.com:8443/") == ("https", "example.com", 8443)
