#!/usr/bin/env python3
"""Scenario B (plan.md 7.3): check storm.

Creates N monitors (default 2000) pointing at the chaos target, half on ``/ok`` and half on
``/slow?ms=500``, with 30-second intervals: about N/30 checks per second (~67/s for 2000).
Afterwards, ``--delete`` removes every monitor whose name starts with ``loadtest-``.

Standard library only, so it runs anywhere Python 3.10+ is available.

Usage:
    export PULSE_ADMIN_API_KEY=...            # never pass the key on the command line
    python3 loadtest/seed_monitors.py --api-url http://localhost:8000
    python3 loadtest/seed_monitors.py --api-url http://localhost:8000 --delete

The target URL must be a host the API allows (PULSE_ALLOW_PRIVATE_HOSTS): ``pulse-target`` in
Kubernetes, ``target`` in docker compose. Only load-test endpoints you own.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

NAME_PREFIX = "loadtest-"
RETRYABLE = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, api_url: str, api_key: str, timeout: float) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json", "X-API-Key": self.api_key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last_error = ""
        for attempt in range(5):
            req = urllib.request.Request(  # noqa: S310 - URL comes from the operator's --api-url
                f"{self.api_url}{path}", data=data, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                last_error = f"{method} {path} -> HTTP {exc.code}: {detail}"
                if exc.code not in RETRYABLE:
                    raise ApiError(last_error) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = f"{method} {path} -> {exc}"
            time.sleep(min(2**attempt * 0.25, 4))
        raise ApiError(f"giving up after retries: {last_error}")

    def list_monitors(self) -> list[dict[str, Any]]:
        body = self.request("GET", "/api/v1/monitors")
        # The API returns a JSON list; tolerate a paginated {"items": [...]} shape too.
        if isinstance(body, dict):
            body = body.get("items", [])
        return list(body or [])


def monitor_body(index: int, target_url: str, interval: int, timeout_ms: int) -> dict[str, Any]:
    path = "/ok" if index % 2 == 0 else "/slow?ms=500"
    return {
        "name": f"{NAME_PREFIX}{index:05d}",
        "url": f"{target_url.rstrip('/')}{path}",
        "method": "GET",
        "interval_seconds": interval,
        "timeout_ms": timeout_ms,
    }


def run_parallel(fn, items, concurrency: int, label: str) -> int:
    ok = failed = 0
    total = len(items)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(fn, item) for item in items]
        for future in as_completed(futures):
            try:
                future.result()
                ok += 1
            except ApiError as exc:
                failed += 1
                if failed <= 10:
                    print(f"  error: {exc}", file=sys.stderr)
            done = ok + failed
            if done % 100 == 0 or done == total:
                print(f"  {label}: {done}/{total} (ok={ok}, failed={failed})")
    return failed


def create(client: Client, args: argparse.Namespace) -> int:
    existing = {m.get("name") for m in client.list_monitors()}
    bodies = [
        monitor_body(i, args.target_url, args.interval, args.timeout_ms)
        for i in range(args.count)
        if f"{NAME_PREFIX}{i:05d}" not in existing
    ]
    skipped = args.count - len(bodies)
    rate = args.count / args.interval
    print(
        f"Creating {len(bodies)} monitors ({skipped} already exist) -> {args.target_url} "
        f"every {args.interval}s (~{rate:.0f} checks/s when all exist)"
    )
    started = time.monotonic()
    failed = run_parallel(
        lambda body: client.request("POST", "/api/v1/monitors", body),
        bodies,
        args.concurrency,
        "created",
    )
    print(f"Done in {time.monotonic() - started:.1f}s; {failed} failed.")
    return 1 if failed else 0


def delete(client: Client, args: argparse.Namespace) -> int:
    total_deleted = 0
    total_failed = 0
    # Loop in case the list endpoint is paginated or capped: keep going until none are left.
    for _ in range(100):
        targets = [
            m for m in client.list_monitors() if str(m.get("name", "")).startswith(NAME_PREFIX)
        ]
        if not targets:
            break
        print(f"Deleting {len(targets)} monitors named {NAME_PREFIX}*")
        failed = run_parallel(
            lambda m: client.request("DELETE", f"/api/v1/monitors/{m['id']}"),
            targets,
            args.concurrency,
            "deleted",
        )
        total_deleted += len(targets) - failed
        total_failed += failed
        if failed == len(targets):
            break  # nothing is succeeding; don't spin
    print(f"Deleted {total_deleted} monitors; {total_failed} failed.")
    return 1 if total_failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("PULSE_API_URL", "http://localhost:8000"),
        help="Pulse API base URL (env PULSE_API_URL; default %(default)s)",
    )
    p.add_argument(
        "--target-url",
        default=os.environ.get("PULSE_TARGET_URL", "http://pulse-target:8080"),
        help="Chaos target base URL the monitors check (env PULSE_TARGET_URL; default %(default)s)",
    )
    p.add_argument(
        "--count", type=int, default=2000, help="number of monitors (default %(default)s)"
    )
    p.add_argument(
        "--interval", type=int, default=30, help="interval_seconds (default %(default)s)"
    )
    p.add_argument("--timeout-ms", type=int, default=5000, help="timeout_ms (default %(default)s)")
    p.add_argument(
        "--concurrency", type=int, default=10, help="parallel API calls (default %(default)s)"
    )
    p.add_argument(
        "--http-timeout", type=float, default=15.0, help="per-request timeout in seconds"
    )
    p.add_argument(
        "--delete", action="store_true", help=f"delete all monitors named {NAME_PREFIX}*"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get("PULSE_ADMIN_API_KEY")
    if not api_key:
        print("PULSE_ADMIN_API_KEY is not set", file=sys.stderr)
        return 2
    if args.count < 1 or args.concurrency < 1:
        print("--count and --concurrency must be >= 1", file=sys.stderr)
        return 2
    client = Client(args.api_url, api_key, args.http_timeout)
    try:
        return delete(client, args) if args.delete else create(client, args)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
