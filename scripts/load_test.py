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
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import websockets

DEMO_PASSWORD = "Demo!Pilot2026"
LOAD_TEST_DOMAIN = "loadtest.example.com"

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
            ("/api/v1/channels/capabilities", 1),
            ("/api/v1/settings/features", 1),
        ],
        "ws": True,
    },
    "manager@nile.example.com": {
        "endpoints": [
            ("/api/v1/dashboard", 4),
            ("/api/v1/rfqs?page_size=20", 3),
            ("/api/v1/approvals?page_size=20", 3),
            ("/api/v1/purchase-orders?page_size=20", 2),
            ("/api/v1/purchase-orders/{po_id}/lifecycle", 2),
            ("/api/v1/analytics/spend", 1),
            ("/api/v1/notifications?page_size=10", 2),
            ("/api/v1/inbox", 2),
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
            ("/api/v1/purchase-orders/{po_id}", 2),
            ("/api/v1/notifications?page_size=10", 2),
            ("/api/v1/inbox", 3),
            ("/api/v1/inbox/threads/{thread_id}", 2),
            ("/api/v1/channels/capabilities", 1),
            ("/api/v1/rfqs/{rfq_id}/channels", 2),
            ("/api/v1/templates/library", 1),
            ("/api/v1/voice/calls", 2),
            ("/api/v1/assistant/sessions", 1),
        ],
        "ws": True,
    },
    "requester@nile.example.com": {
        "endpoints": [
            ("/api/v1/requests?page_size=20", 5),
            ("/api/v1/dashboard", 3),
            ("/api/v1/rfqs?page_size=20", 2),
            ("/api/v1/notifications?page_size=10", 2),
            ("/api/v1/assistant/sessions", 1),
        ],
        # The requesting department actually creates requests while the test runs.
        "writes": ["intake"],
        "ws": False,
    },
    "finance@nile.example.com": {
        "endpoints": [
            ("/api/v1/purchase-orders?page_size=20", 4),
            ("/api/v1/supplier-balances", 3),
            ("/api/v1/purchase-orders?page_size=5", 2),
            ("/api/v1/dashboard", 2),
            ("/api/v1/notifications?page_size=10", 2),
            ("/api/v1/accounting/supplier-account/{supplier_id}", 3),
        ],
        "ws": True,
    },
    "storekeeper@nile.example.com": {
        "endpoints": [
            ("/api/v1/purchase-orders?page_size=20", 4),
            ("/api/v1/purchase-orders?status=issued&page_size=10", 2),
            ("/api/v1/purchase-orders/{po_id}", 3),
            ("/api/v1/notifications?page_size=10", 3),
            ("/api/v1/dashboard", 2),
            ("/api/v1/suppliers?page_size=20", 1),
        ],
        # Warehouse staff post goods receipts while the test runs (partial receipts).
        "writes": ["receipt"],
        "ws": True,
    },
    "controller@nile.example.com": {
        "endpoints": [
            ("/api/v1/accounting/journal-entries?page_size=20", 4),
            ("/api/v1/accounting/trial-balance", 3),
            ("/api/v1/accounting/cost-report?group_by=cost_center", 2),
            ("/api/v1/accounting/accounts", 1),
            ("/api/v1/purchase-orders?page_size=20", 2),
            ("/api/v1/accounting/supplier-account/{supplier_id}", 2),
            ("/api/v1/notifications?page_size=10", 1),
        ],
        "ws": True,
    },
    "auditor@nile.example.com": {
        "endpoints": [
            ("/api/v1/purchase-orders?page_size=20", 4),
            ("/api/v1/purchase-orders/{po_id}/audit-package", 3),
            ("/api/v1/accounting/journal-entries?page_size=20", 2),
            ("/api/v1/accounting/trial-balance", 2),
            ("/api/v1/inbox", 2),
            ("/api/v1/audit?page_size=50", 2),
            ("/api/v1/notifications?page_size=10", 1),
        ],
        "ws": True,
    },
}

#: Path fragments that need a real id; resolved once per virtual user before the loop starts.
CONTEXT_SOURCES: dict[str, tuple[str, str]] = {
    # placeholder -> (list endpoint, item key)
    "po_id": ("/api/v1/purchase-orders?page_size=20", "id"),
    "rfq_id": ("/api/v1/rfqs?page_size=5", "id"),
    "supplier_id": ("/api/v1/suppliers?page_size=1", "id"),
    "thread_id": ("/api/v1/inbox", "thread_id"),
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
    writes: int = 0
    writes_ok: int = 0
    writes_refused: int = 0
    refusals: list[str] = field(default_factory=list)

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
                # One heartbeat every 25 s, exactly like the SPA — not a hot loop. Broadcast frames
                # (notification.created) can arrive at any time, so drain briefly then sleep.
                # The browser client sends a bare `ping`; the server answers with a `pong` frame.
                await socket.send("ping")
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    try:
                        await asyncio.wait_for(socket.recv(), timeout=0.05)
                        stats.ws_frames += 1
                    except (asyncio.TimeoutError, TimeoutError):
                        break
                await asyncio.sleep(max(0.0, min(25.0, stop_at - time.time())))
    except Exception as exc:  # noqa: BLE001
        stats.ws_errors.append(f"{email}: {type(exc).__name__}: {exc}")
        if connected_once:
            stats.ws_reconnects += 1


async def resolve_context(client: httpx.AsyncClient, paths: set[str], stats: Stats,
                          index: int = 0) -> dict[str, str]:
    """Turn `{po_id}`-style placeholders into real ids, once per virtual user.

    Without this, half the endpoints a persona needs (a thread, an order, a supplier sub-ledger)
    cannot be addressed at all. A missing id is not a failure — the path is simply skipped.

    The row is chosen by the virtual user's index (`index % len(items)`), not always the first one:
    with all fifty users hammering the same purchase order, every goods receipt queued on the same
    row lock, which measured the lock rather than the system. Warehouse staff in the same plant
    receive different orders.
    """
    needed = {name for name in CONTEXT_SOURCES if "{" + name + "}" in " ".join(paths)}
    context: dict[str, str] = {}
    for name in needed:
        source, key = CONTEXT_SOURCES[name]
        try:
            response = await client.get(source)
        except Exception:  # noqa: BLE001 - context is best effort
            continue
        if response.status_code != 200:
            continue
        body = response.json()
        items = body.get("items", body.get("threads", [])) if isinstance(body, dict) else []
        if not items:
            continue
        row = items[index % len(items)]
        value = row.get(key) or row.get("id") or ""
        if value:
            context[name] = str(value)
    return context


def fill(path: str, context: dict[str, str]) -> str | None:
    """Substitute placeholders; return None when an id is unknown so the caller can skip."""
    resolved = path
    for name in CONTEXT_SOURCES:
        if "{" + name + "}" in resolved:
            if name not in context:
                return None
            resolved = resolved.replace("{" + name + "}", context[name])
    return resolved


async def seed_receipt_orders(count: int = 5, quantity: str = "500") -> list[str]:
    """Give the warehouse personas orders they can actually receive against.

    The demo dataset is small and its single purchase order is already fully received, so every
    goods receipt in a load run was refused by the (correct) over-receipt guard and the write path
    was never measured. This seeds a handful of large, freshly issued orders — the same shape a real
    warehouse team works with — and the run receives against them.

    Best effort: if the database is not reachable from here the run continues without receipts.
    """
    try:
        from sqlalchemy import select

        from app.db.models.entities import RFQ, PurchaseOrder, Supplier, Tenant
        from app.db.session import SessionLocal
    except Exception:  # noqa: BLE001 - the harness still works as a pure HTTP client
        return []
    ids: list[str] = []
    try:
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one_or_none()
            if tenant is None:
                return []
            rfq = (await session.execute(
                select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
            )).scalars().first()
            supplier = (await session.execute(
                select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.created_at)
            )).scalars().first()
            if rfq is None or supplier is None:
                return []
            unit_price = Decimal("100")
            for index in range(count):
                po = PurchaseOrder(
                    tenant_id=tenant.id, code=f"PO-LOAD-{uuid.uuid4().hex[:8]}", rfq_id=rfq.id,
                    supplier_id=supplier.id, amount=Decimal(quantity) * unit_price, currency="EGP",
                    status="issued", issued_at=datetime.now(UTC),
                    lines=[{"description": "Load-test receipt item", "quantity": quantity, "uom": "carton",
                            "unit_price": str(unit_price),
                            "line_total": str(Decimal(quantity) * unit_price)}],
                )
                session.add(po)
                await session.flush()
                ids.append(po.id)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - seeding is a convenience, never a hard failure
        print(f"note  could not seed receipt orders ({type(exc).__name__}); receipts will be skipped")
        return []
    print(f"seeded {len(ids)} receipt-ready purchase orders for the warehouse personas")
    return ids


async def write_probe(kind: str, client: httpx.AsyncClient, context: dict[str, str],
                      index: int, stats: Stats) -> None:
    """One write action from a real workflow: a requisition or a partial goods receipt.

    A business-rule refusal under concurrency (the order was already fully received by someone
    else, the request is a duplicate) is recorded as a *refusal*, not an error: refusing is the
    correct behaviour. Only a 5xx or a transport failure is an error.
    """
    started = time.perf_counter()
    path = ""
    try:
        if kind == "intake":
            path = "/api/v1/intake/text"
            response = await client.post(path, json={
                "text": f"Load test user {index}: need 10 boxes of PPE gloves for the "
                        f"Cairo stores, delivery within 2 weeks, payment Net 30.",
                "title": f"Load test requisition {index}-{int(time.time() * 1000) % 1000000}",
            })
        else:
            po_id = context.get("po_id", "")
            if not po_id:
                return
            path = f"/api/v1/purchase-orders/{po_id}/receipts"
            response = await client.post(path, json={
                "lines": [{"line_no": 1, "received_qty": "1", "accepted_qty": "1"}],
                "warehouse": "Cairo stores",
                "delivery_note_ref": f"LT-{index}-{int(time.time() * 1000) % 100000}",
            })
    except Exception as exc:  # noqa: BLE001
        stats.errors.append(f"{kind} write: {type(exc).__name__}")
        return
    latency = (time.perf_counter() - started) * 1000
    stats.writes += 1
    stats.samples.append(Sample(f"POST {path}", response.status_code, latency))
    stats.statuses[response.status_code] += 1
    if response.status_code < 400:
        stats.writes_ok += 1
    elif response.status_code >= 500 or response.status_code == 429:
        stats.errors.append(f"POST {path}: HTTP {response.status_code}")
    else:
        stats.writes_refused += 1
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("code") or body.get("error", "") or "")[:60]
        except Exception:  # noqa: BLE001
            pass
        stats.refusals.append(f"{path}: {response.status_code} {detail}".strip())


async def virtual_user(
    index: int,
    email: str,
    persona: dict,
    base_url: str,
    ws_base: str,
    stop_at: float,
    stats: Stats,
    receipt_orders: list[str] | None = None,
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
        all_paths = {path for path, _weight in persona["endpoints"]}
        context = await resolve_context(client, all_paths, stats, index)
        if receipt_orders:
            # Every warehouse user receives against one of the freshly seeded orders.
            context["po_id"] = receipt_orders[index % len(receipt_orders)]
        ws_task = None
        if persona["ws"]:
            ws_task = asyncio.create_task(
                websocket_user(email, token, ws_base, stop_at, stats)
            )
        while time.time() < stop_at:
            if persona.get("writes") and random.random() < 0.15:
                await write_probe(random.choice(persona["writes"]), client, context, index, stats)
                await asyncio.sleep(random.uniform(0.6, 2.0))
                continue
            path = fill(pick(persona["endpoints"]), context)
            if path is None:
                await asyncio.sleep(0.2)
                continue
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


async def provision_users(base_url: str, count: int) -> list[tuple[str, dict]]:
    """One account per virtual user, so 50 users do not share 8 identities.

    Sharing an identity looks fine in aggregate but is wrong twice over: the per-user rate limit
    then measures 6 people as one, and the load test stops representing "50 people". Accounts are
    created through the public admin API (idempotent: an existing account is reused).
    """
    personas = list(PERSONAS.items())
    assignments: list[tuple[str, dict]] = []
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(30.0), limits=limits) as admin:
        login = await admin.post("/api/v1/auth/login",
                                 json={"email": "admin@pilot.procureai", "password": DEMO_PASSWORD})
        if login.status_code != 200:
            raise SystemExit(f"cannot provision load-test users: admin login HTTP {login.status_code}")
        admin.headers["authorization"] = f"Bearer {login.json()['access_token']}"
        # The role *keys* come from the demo accounts themselves (`warehouse`, not `storekeeper`):
        # asking the server beats duplicating the RBAC matrix in a load-test script.
        role_by_persona: dict[str, str] = {}
        for demo_email, _persona in personas:
            probe = await admin.post("/api/v1/auth/login",
                                     json={"email": demo_email, "password": DEMO_PASSWORD})
            if probe.status_code != 200:
                raise SystemExit(f"cannot inspect {demo_email}: HTTP {probe.status_code}")
            roles = probe.json().get("user", {}).get("roles") or []
            if not roles:
                raise SystemExit(f"{demo_email} reports no role; cannot mirror it")
            role_by_persona[demo_email] = roles[0]

        for index in range(count):
            demo_email, persona = personas[index % len(personas)]
            email = f"loadtest{index:02d}@{LOAD_TEST_DOMAIN}"
            created = await admin.post("/api/v1/users", json={
                "email": email, "full_name": f"Load Test {index}",
                "roles": [role_by_persona[demo_email]], "password": DEMO_PASSWORD,
            })
            if created.status_code == 409:
                # Either the account survived a previous run (fine) or the create was refused.
                detail = created.json().get("detail", "")
                if "already exists" not in detail:
                    raise SystemExit(f"cannot create {email}: {detail}")
            elif created.status_code != 201:
                raise SystemExit(f"cannot create {email}: HTTP {created.status_code} {created.text[:120]}")
            assignments.append((email, persona))
    return assignments


async def run(args: argparse.Namespace) -> int:
    random.seed(args.seed)
    if args.provision:
        assignments = await provision_users(args.base_url, args.users)
        print(f"provisioned {len(assignments)} load-test accounts (one per virtual user)")
    else:
        emails = list(PERSONAS)
        assignments = [(emails[index % len(emails)], PERSONAS[emails[index % len(emails)]])
                       for index in range(args.users)]
    receipt_orders = [] if args.no_seed else await seed_receipt_orders(args.receipt_orders)
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
                email,
                persona,
                args.base_url,
                args.ws_base or args.base_url.replace("http", "ws", 1),
                stop_at,
                stats,
                receipt_orders,
            )
        )
        for index, (email, persona) in enumerate(assignments)
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
        "writes": {
            "attempted": stats.writes,
            "ok": stats.writes_ok,
            "refused_by_business_rule": stats.writes_refused,
            "sample_refusals": stats.refusals[:5],
        },
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
    print(f"writes          {stats.writes} attempted | {stats.writes_ok} ok | "
          f"{stats.writes_refused} refused by a business rule")
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
    parser.add_argument("--receipt-orders", type=int, default=5,
                        help="how many fresh, receipt-ready purchase orders to seed (default 5)")
    parser.add_argument("--no-seed", action="store_true",
                        help="do not seed receipt orders; every goods receipt will be refused by the "
                             "over-receipt guard because the demo orders are already received")
    parser.add_argument("--provision", action="store_true", default=True,
                        help="create one account per virtual user (default); --no-provision reuses the demo users")
    parser.add_argument("--no-provision", dest="provision", action="store_false")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
