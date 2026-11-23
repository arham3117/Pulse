"""Seed the local stack with demo monitors (`make seed`).

Stdlib only, so it runs with any Python 3.12 without installing the app. Idempotent:
monitors that already exist (same name) are skipped.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("PULSE_API_URL", "http://localhost:8000")
KEY = os.environ.get("PULSE_ADMIN_API_KEY", "local-dev-key")  # matches docker-compose.yml

MONITORS = [
    {"name": "Target /ok", "url": "http://target:8080/ok", "interval_seconds": 30},
    {"name": "Target /slow 800ms", "url": "http://target:8080/slow?ms=800", "interval_seconds": 30},
    {
        "name": "Target /flaky 20%",
        "url": "http://target:8080/flaky?fail_rate=0.2",
        "interval_seconds": 30,
    },
    {
        "name": "Target /switch",
        "url": "http://target:8080/switch",
        "interval_seconds": 30,
        "keyword": "PULSE_OK",
        "failure_threshold": 3,
    },
    {"name": "example.com", "url": "https://example.com/", "interval_seconds": 60},
]


def request(method: str, path: str, body: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - fixed local API URL
        f"{API}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read() or b"null")


def main() -> int:
    try:
        existing = {m["name"] for m in request("GET", "/api/v1/monitors")}  # type: ignore[union-attr]
    except urllib.error.URLError as exc:
        print(f"API not reachable at {API}: {exc}. Is `make up` running?", file=sys.stderr)
        return 1
    for monitor in MONITORS:
        if monitor["name"] in existing:
            print(f"exists   {monitor['name']}")
            continue
        try:
            created = request("POST", "/api/v1/monitors", monitor)
            print(f"created  {monitor['name']}  {created['id']}")  # type: ignore[index]
        except urllib.error.HTTPError as exc:
            print(f"failed   {monitor['name']}: {exc.code} {exc.read().decode()}", file=sys.stderr)
    print(f"\nStatus page: {API}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
