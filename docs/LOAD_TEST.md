# Load test — 50 concurrent users

`scripts/load_test.py` is the harness that produced these numbers. It is not a synthetic
`ab`-style flood: every virtual user is a **separate HTTP connection pool with its own login
session, its own role and its own think time**, and a share of them hold a WebSocket open for the
whole run — the same shape as 50 people using the browser app.

```bash
# PostgreSQL 16 must be up and migrated first (see docs/RUNBOOK.md §2a)
python scripts/load_test.py --users 50 --duration 60 --json /tmp/load.json
```

## What a virtual user does

| Persona (real demo account) | Endpoints it reads | WebSocket |
| --- | --- | --- |
| `admin@pilot.procureai` | dashboard, requests, RFQs, POs, suppliers, approvals, KPIs, notifications | yes |
| `manager@nile.example.com` | dashboard, RFQs, approvals, POs, spend, notifications | yes |
| `buyer@nile.example.com` | RFQs, requests, RFQ board, suppliers, POs, notifications | yes |
| `requester@nile.example.com` | requests, dashboard, RFQs, notifications | no |
| `finance@nile.example.com` | POs, supplier balances, notification feed | yes |
| `storekeeper@nile.example.com` | POs, notifications, dashboard, suppliers | yes |

Each persona only calls endpoints its role may read, so a 403 anywhere is a failure — the run
doubles as a live check of the RBAC map. Think time is a uniform 0.4–1.8 s per request, and the 50
users ramp up over 10 s (so the 71.5 s wall clock below = 10 s ramp + 60 s load + drain).

## Result — 50 users, 60 s, PostgreSQL 16.2 (single uvicorn worker, 10+20 connection pool)

```
requests        2928  (40.9 req/s over 71.6s)
errors          0  (0.00 %)
latency ms      p50 14 | p95 116 | p99 393 | max 1036
status          {'200': 2928}
websockets      42/42 connected, 168 frames, 0 reconnects
logins          50 ok / 0 failed
------------------------------------------------------------------------
  /api/v1/analytics/kpis                         n=  22 p50=  15.5 p95=  50.0
  /api/v1/analytics/spend                        n=  36 p50=  16.2 p95=  35.0
  /api/v1/approvals?page_size=20                 n= 154 p50=  14.3 p95=  61.5
  /api/v1/dashboard                              n= 532 p50=  29.7 p95= 190.3
  /api/v1/notifications?page_size=10             n= 470 p50=  11.5 p95= 126.8
  /api/v1/purchase-orders?page_size=20           n= 574 p50=  11.9 p95=  52.4
  /api/v1/purchase-orders?page_size=5            n=  60 p50=  12.9 p95= 140.0
  /api/v1/requests?page_size=20                  n= 353 p50=  10.2 p95=  87.7
  /api/v1/rfqs/board                             n=  65 p50=   9.9 p95= 123.3
  /api/v1/rfqs?page_size=20                      n= 392 p50=  11.0 p95=  57.5
  /api/v1/supplier-balances                      n= 109 p50=  12.8 p95=  64.8
  /api/v1/suppliers?page_size=20                 n= 161 p50=  49.7 p95= 117.5
------------------------------------------------------------------------
  [PASS] p95 < 800 ms
  [PASS] p99 < 2000 ms
  [PASS] error rate < 1 %
  [PASS] websocket connects 100 %
  [PASS] no 5xx responses
RESULT: PASS
```

**Interpretation.** p95 of 116 ms and p99 of 393 ms mean the database round trip, not the API layer,
is the tail: `/dashboard`, `/suppliers` and `/notifications` are the heaviest endpoints (they
aggregate across tables and decrypt supplier contacts), and all three stay under 200 ms at p95 while
50 users hammer them. Zero 5xx, zero 429s and zero pool timeouts at 41 req/s confirms the 10+20 pool
is sized generously for this population; 42 simultaneous WebSockets were accepted and answered
every heartbeat (168 frames = 42 sockets × 2 heartbeats + broadcasts).

**Two real defects this test found** (both fixed on this branch):

1. `GET /suppliers` returned 500 under load once a tenant's encrypted contacts could not be
   decrypted (a master-key mismatch after the demo data had been seeded with a different secret) —
   one unreadable row took the whole screen down. Decryption now fails closed (`CryptoError` →
   empty PII, one error log per tenant) and the list renders; covered by
   `tests/test_crypto_resilience.py`.
2. The harness itself was pinging in a hot loop (937k frames), which inflated latency to p50 47 ms /
   p95 456 ms. It now sends one heartbeat every 25 s like the SPA, and the numbers above are from
   the corrected loop.

Pass criteria are enforced by the script's exit code, so the same command can gate a release.

**Interpretation.** p95 of 82 ms and p99 of 317 ms mean the database round trip, not the API layer,
is the tail: `/dashboard`, `/supplier-balances` and `/suppliers` are the three heaviest endpoints
(they aggregate across tables), and all three stay under 140 ms at p95 while 50 users hammer them.
Zero 5xx and zero pool timeouts at 41 req/s confirms the 10+20 pool is sized generously for this
population; 42 simultaneous WebSockets were accepted and answered every heartbeat.

## Caveats (kept explicit)

* One uvicorn process, one PostgreSQL instance on the same machine, no Redis (rate limiting was
  disabled for the run), no TLS, no reverse proxy. A production deployment adds a hop; it also adds
  replicas and a connection pooler.
* Reads dominate because a human browsing the app issues reads. Write-heavy paths (intake, dispatch,
  approval decisions) are covered by the functional suites, not by this run — flooding them would
  mutate the demo dataset.
* `python scripts/load_test.py --users 200` will exceed a single worker long before it saturates
  PostgreSQL; scale the API (replicas) and add the Redis pub/sub fan-out for the WebSocket hub
  (`docs/API.md` → WebSocket) before raising the number.
