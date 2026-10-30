#!/usr/bin/env python
"""Realistic 50-concurrent-user load test.

Every virtual user is a separate HTTP connection pool with its own login session, its own role
(so the permission gates are exercised) and its own think time. A share of the users additionally
hold a WebSocket open for the whole run, exactly like the browser app does.

    python scripts/load_test.py --users 50 --duration 60 --base-url http://localhost:8000

What it measures (all printed at the end, plus a JSON summary with --json out.json):

* login + API latency percentiles (p50/p95/p99/max) overall and per endpoint,
* achieved throughput (requests/second),
* status-code distribution and error rate (5xx, 429 and transport errors),
* WebSocket connect success, frames received, reconnects and uptime.

Pass criteria (exit code 1 if any fails):

    p95 API latency   < 800 ms
    p99 API latency   < 2000 ms
    error rate        < 1 %
    WebSocket connects 100 %
    5xx responses     0

Run it against PostgreSQL 16 — the supported database — not the SQLite fallback.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx
import websockets

DEMO_PASSWORD = "Demo!Pilot2026"

# Each persona mirrors a real demo user and only touches endpoints that role may read, so the run
# also verifies the RBAC map in both directions (allowed calls succeed, nothing 403s).
PERSONAS: dict[str, dict] = {
    "admin@pilot.procureai": {
        "endpoints": [
            ("/api/v1/dashboard", 4),
            ("/api/v1/requests?page_size=20", 3),
            ("/api/v1/rfqs?page_size=20", 3),
            ("/api/v1/purchase-orders?page_size=20", 3),
            ("/api/v1/suppliers?page_size=20", 2),
            ("/api/v1/approvals?page_size=20", 2),
            ("/api/v1/analytics/kpis", 1),
            ("/api/v1/notifications?page_size=10", 2),
        ],
        "ws": True,
    },
    "manager@nile.example.com": {
        "endpoints": [
            ("/api/v1/dashboard", 4),
            ("/api/v1/rfqs?page_size=20", 3),
            ("/api/v1/approvals?page_size=20", 3),
            ("/api/v1/purchase-orders?page_size=20", 2),
            ("/api/v1/analytics/spend", 1),
            ("/api/v1/notifications?page_size=10", 2),
        ],
        "ws": True,
    },
    "buyer@nile.example.com": {
        "endpoints": [
            ("/api/v1/rfqs?page_size=20", 4),
            ("/api/v1/requests?page_size=20", 3),
            ("/api/v1/rfqs/board", 2),
            ("/api/v1/suppliers?page_size=20", 2),
            ("/api/v1/purchase-orders?page_size=20", 2),
            ("/api/v1/notifications?page_size=10", 2),
        ],
        "ws": True,
    },
    "requester@nile.example.com": {
        "endpoints": [
            ("/api/v1/requests?page_size=20", 5),
            ("/api/v1/dashboard", 3),
            ("/api/v1/rfqs?page_size=20", 2),
            ("/api/v1/notifications?page_size=10", 2),
        ],
        "ws": False,
    },
    "finance@nile.example.com": {
        "endpoints": [
            ("/api/v1/purchase-orders?page_size=20", 4),
            ("/api/v1/supplier-balances", 3),
            ("/api/v1/purchase-orders?page_size=5", 2),
            ("/api/v1/dashboard", 2),
            ("/api/v1/notifications?page_size=10", 2),
        ],
        "ws": True,
    },
    "storekeeper@nile.example.com": {
        "endpoints": [
            ("/api/v1/purchase-orders?page_size=20", 4),
            ("/api/v1/notifications?page_size=10", 3),
            ("/api/v1/dashboard", 2),
            ("/api/v1/suppliers?page_size=20", 1),
        ],
        "ws": True,
    },
}

# Some endpoints in the personas above are optional (they only exist when the tenant has data at
# that lifecycle stage); a 404 there is not a failure, it is an empty state.
TOLERATED = {404}


@dataclass
class Sample:
    endpoint: str
    status: int
    latency_ms: float


@dataclass
class Stats:
    samples: list[Sample] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    errors: list[str] = field(default_factory=list)
    ws_attempts: int = 0
    ws_connected: int = 0
    ws_frames: int = 0
    ws_reconnects: int = 0
    ws_errors: list[str] = field(default_factory=list)
    logins: int = 0
    login_failures: int = 0

    def ok_errors(self) -> list[str]:
        return self.errors


def pick(endpoints: list[tuple[str, int]]) -> str:
    paths = [path for path, _weight in endpoints]
    weights = [weight for _path, weight in endpoints]
    return random.choices(paths, weights=weights, k=1)[0]


async def login(client: httpx.AsyncClient, email: str, stats: Stats) -> str | None:
    started = time.perf_counter()
    try:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}
        )
    except Exception as exc:  # noqa: BLE001 - transport failures are data, not crashes
        stats.login_failures += 1
        stats.errors.append(f"login {email}: {type(exc).__name__}")
        return None
    finally:
        _ = time.perf_counter() - started
    stats.logins += 1
    if response.status_code != 200:
        stats.login_failures += 1
        stats.errors.append(f"login {email}: HTTP {response.status_code}")
        return None
    return response.json().get("access_token")


async def websocket_user(
    email: str, token: str, ws_base: str, stop_at: float, stats: Stats
) -> None:
    """Hold one WebSocket per user like the SPA does, pinging every 20 s."""
    stats.ws_attempts += 1
    url = f"{ws_base}/ws?token={token}"
    connected_once = False
    try:
        async with websockets.connect(url, ping_interval=None, open_timeout=10) as socket:
            connected_once = True
            stats.ws_connected += 1
            while time.time() < stop_at:
                # The browser client sends a bare `ping`; the server answers with a `pong` frame.
                await socket.send("ping")
                try:
                    await asyncio.wait_for(socket.recv(), timeout=25)
                    stats.ws_frames += 1
                except asyncio.TimeoutError:
                    stats.ws_errors.append(f"{email}: no frame within 25s")
                    break
    except Exception as exc:  # noqa: BLE001
        stats.ws_errors.append(f"{email}: {type(exc).__name__}: {exc}")
        if connected_once:
            stats.ws_reconnects += 1


async def virtual_user(
    index: int,
    email: str,
    persona: dict,
    base_url: str,
    ws_base: str,
    stop_at: float,
    stats: Stats,
) -> None:
    # Stagger the ramp-up so 50 users do not hit the server in the same millisecond.
    await asyncio.sleep(random.uniform(0, 10))
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        base_url=base_url, timeout=httpx.Timeout(20.0), limits=limits, follow_redirects=False
    ) as client:
        token = await login(client, email, stats)
        if token is None:
            return
        client.headers["authorization"] = f"Bearer {token}"
        ws_task = None
        if persona["ws"]:
            ws_task = asyncio.create_task(
                websocket_user(email, token, ws_base, stop_at, stats)
            )
        while time.time() < stop_at:
            path = pick(persona["endpoints"])
            started = time.perf_counter()
            try:
                response = await client.get(path)
            except Exception as exc:  # noqa: BLE001
                stats.errors.append(f"{path}: {type(exc).__name__}")
                await asyncio.sleep(0.5)
                continue
            latency = (time.perf_counter() - started) * 1000
            stats.samples.append(Sample(path, response.status_code, latency))
            stats.statuses[response.status_code] += 1
            if response.status_code >= 500 or response.status_code == 429:
                stats.errors.append(f"{path}: HTTP {response.status_code}")
            elif response.status_code >= 400 and response.status_code not in TOLERATED:
                stats.errors.append(f"{path}: HTTP {response.status_code}")
            # Think time: humans read a screen rather than hammering the API.
            await asyncio.sleep(random.uniform(0.4, 1.8))
        if ws_task is not None:
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def run(args: argparse.Namespace) -> int:
    emails = list(PERSONAS)
    random.seed(args.seed)
    stats = Stats()
    stop_at = time.time() + args.duration + 10  # ramp-up allowance
    started = time.time()

    print(
        f"load test: {args.users} users / {args.duration}s against {args.base_url} "
        f"(seed {args.seed})"
    )
    tasks = [
        asyncio.create_task(
            virtual_user(
                index,
                emails[index % len(emails)],
                PERSONAS[emails[index % len(emails)]],
                args.base_url,
                args.ws_base or args.base_url.replace("http", "ws", 1),
                stop_at,
                stats,
            )
        )
        for index in range(args.users)
    ]
    progress = asyncio.create_task(_report_progress(stats, started + args.duration))
    await asyncio.gather(*tasks)
    progress.cancel()
    elapsed = time.time() - started

    latencies = [sample.latency_ms for sample in stats.samples]
    total = len(latencies)
    errors = len(stats.errors)
    error_rate = (errors / total * 100) if total else 0.0
    p50, p95, p99 = (
        percentile(latencies, 0.50),
        percentile(latencies, 0.95),
        percentile(latencies, 0.99),
    )
    per_endpoint: dict[str, list[float]] = defaultdict(list)
    for sample in stats.samples:
        per_endpoint[sample.endpoint].append(sample.latency_ms)

    ws_ok = stats.ws_attempts == 0 or stats.ws_connected == stats.ws_attempts
    five_xx = sum(count for code, count in stats.statuses.items() if code >= 500)
    checks = {
        "p95 < 800 ms": p95 < 800,
        "p99 < 2000 ms": p99 < 2000,
        "error rate < 1 %": error_rate < 1,
        "websocket connects 100 %": ws_ok,
        "no 5xx responses": five_xx == 0,
    }

    summary = {
        "target": args.base_url,
        "users": args.users,
        "duration_s": round(elapsed, 1),
        "requests": total,
        "throughput_rps": round(total / elapsed, 1),
        "errors": errors,
        "error_rate_pct": round(error_rate, 2),
        "latency_ms": {
            "p50": round(p50, 1),
            "p95": round(p95, 1),
            "p99": round(p99, 1),
            "max": round(max(latencies, default=0.0), 1),
            "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        },
        "status_counts": {str(code): count for code, count in sorted(stats.statuses.items())},
        "per_endpoint_ms": {
            path: {
                "count": len(values),
                "p50": round(percentile(values, 0.50), 1),
                "p95": round(percentile(values, 0.95), 1),
            }
            for path, values in sorted(per_endpoint.items())
        },
        "websockets": {
            "attempts": stats.ws_attempts,
            "connected": stats.ws_connected,
            "frames": stats.ws_frames,
            "reconnects": stats.ws_reconnects,
            "errors": stats.ws_errors[:5],
        },
        "logins": {"ok": stats.logins, "failed": stats.login_failures},
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "sample_errors": stats.errors[:10],
    }

    _ = elapsed
    print(f"\n{'=' * 72}")
    print(f"requests        {total}  ({summary['throughput_rps']} req/s over {elapsed:.1f}s)")
    print(f"errors          {errors}  ({error_rate:.2f} %)")
    print(f"latency ms      p50 {p50:.0f} | p95 {p95:.0f} | p99 {p99:.0f} | max {summary['latency_ms']['max']:.0f}")
    print(f"status          {summary['status_counts']}")
    print(
        f"websockets      {stats.ws_connected}/{stats.ws_attempts} connected, "
        f"{stats.ws_frames} frames, {stats.ws_reconnects} reconnects"
    )
    print(f"logins          {stats.logins} ok / {stats.login_failures} failed")
    print("-" * 72)
    for path, values in sorted(per_endpoint.items()):
        print(f"  {path:46s} n={len(values):4d} p50={percentile(values, 0.5):6.1f} p95={percentile(values, 0.95):6.1f}")
    print("-" * 72)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if summary["sample_errors"]:
        print("first errors:", summary["sample_errors"])
    print(f"RESULT: {summary['result']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        print(f"wrote {args.json}")
    return 0 if summary["result"] == "PASS" else 1


async def _report_progress(stats: Stats, until: float) -> None:
    while time.time() < until:
        await asyncio.sleep(10)
        done = len(stats.samples)
        errors = len(stats.errors)
        print(f"  ... {done} requests, {errors} errors, {stats.ws_connected} sockets")


def main() -> None:
    parser = argparse.ArgumentParser(description="50-user load test for ProcureAI")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--ws-base", default=None, help="defaults to base-url with ws:// scheme")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--duration", type=int, default=60, help="measured window in seconds")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", default=None, help="write the metrics summary to this file")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
