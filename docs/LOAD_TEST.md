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
requests        2956  (41.3 req/s over 71.5s)
errors          0  (0.00 %)
latency ms      p50 10 | p95 82 | p99 317 | max 711
status          {'200': 2956}
websockets      42/42 connected, 42 frames, 0 reconnects
logins          50 ok / 0 failed
------------------------------------------------------------------------
  /api/v1/analytics/kpis                         n=  15 p50=  12.4 p95=  18.2
  /api/v1/analytics/spend                        n=  36 p50=  11.0 p95=  25.9
  /api/v1/approvals?page_size=20                 n= 151 p50=   9.7 p95=  68.9
  /api/v1/dashboard                              n= 522 p50=  21.8 p95= 126.3
  /api/v1/notifications?page_size=10             n= 453 p50=   7.8 p95=  75.2
  /api/v1/purchase-orders?page_size=20           n= 568 p50=   8.3 p95=  76.6
  /api/v1/purchase-orders?page_size=5            n=  74 p50=   8.3 p95=  14.0
  /api/v1/requests?page_size=20                  n= 386 p50=   7.6 p95=  90.1
  /api/v1/rfqs/board                             n=  48 p50=   6.6 p95=  21.1
  /api/v1/rfqs?page_size=20                      n= 432 p50=   7.3 p95=  34.7
  /api/v1/supplier-balances                      n= 110 p50=   8.8 p95= 136.6
  /api/v1/suppliers?page_size=20                 n= 161 p50=  38.0 p95= 125.4
------------------------------------------------------------------------
  [PASS] p95 < 800 ms
  [PASS] p99 < 2000 ms
  [PASS] error rate < 1 %
  [PASS] websocket connects 100 %
  [PASS] no 5xx responses
RESULT: PASS
```

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
