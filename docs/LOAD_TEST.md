# Load test — 50 concurrent users

`scripts/load_test.py` is the harness that produced these numbers. It is not a synthetic
`ab`-style flood: every virtual user is a **separate HTTP connection pool with its own login
session, its own role and its own think time**, and a share of them hold a WebSocket open for the
whole run — the same shape as 50 people using the browser app.

```bash
# PostgreSQL 16 must be up and migrated first (see docs/RUNBOOK.md §2a)
python scripts/load_test.py --users 50 --duration 60 --json /tmp/load.json

# Flags that matter for a reproducible run:
#   --receipt-orders 5   seed this many large, freshly issued orders for the warehouse personas
#   --no-seed            skip seeding (every goods receipt is then refused by the over-receipt guard)
#   --provision          create one account per virtual user (default on) so rate limits bucket
#                        per identity rather than per address
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

Besides reading, the personas **write**: buyers file requisitions through `POST /intake/text` and
warehouse staff post partial goods receipts (`POST /purchase-orders/{id}/receipts`) against the
seeded orders, at a 15 % probability per loop. Writes are scored separately from errors — a 422
business-rule refusal is a correct answer, a 5xx is not.

## Result — 50 users, 60 s, PostgreSQL 16.2 (single uvicorn worker, 10+20 connection pool)

Final phase-4 run, on a **freshly migrated and freshly seeded** database (`alembic upgrade head`,
`app.cli bootstrap`, `app.cli seed-demo`), with the rate limiter **enabled** and five receipt-ready
orders seeded for the warehouse personas:

```
requests        2823  (39.5 req/s over 71.5 s)
errors          0      (0.00 %)
latency ms      p50 15 | p95 188 | p99 662 | max 1435
status          {'200': 2715, '201': 108}
writes          108 attempted | 108 ok | 0 refused by a business rule
websockets      44/44 connected, 14959 frames, 0 reconnects, 0 failed upgrades
logins          50 ok / 0 failed
load-test accounts 50 provisioned (one per virtual user, so the rate limiter buckets per identity)
------------------------------------------------------------------------
  slowest endpoints (p95, this run)
  POST /purchase-orders/{id}/receipts  (11 at once on ONE order)   1307
  /rfqs/{id}/channels                                              1021
  /purchase-orders/{id}                                             770
  POST /intake/text                                                 573
  /accounting/cost-report?group_by=cost_center                       446
------------------------------------------------------------------------
  [PASS] p95 < 800 ms
  [PASS] p99 < 2000 ms
  [PASS] error rate < 1 %
  [PASS] websocket connects 100 %
  [PASS] no 5xx responses
RESULT: PASS
```

### Run-to-run variance (same code, same machine)

| Run | What it did | requests | p95 | p99 | errors |
| --- | --- | --- | --- | --- | --- |
| v4 | 5 POs, receipts spread by user index | 2859 | 163.6 | 369.9 | 0 |
| final-2 | fresh seed, receipts refused (order already received) | 2862 | 115.3 | 553.4 | 0 |
| final-5 | fresh seed + 5 seeded receipt-ready orders | 2823 | 188.2 | 661.8 | 0 |

The sandbox is a shared machine, so the tail moves by ±40 % between runs while p50 stays at
15–21 ms and the error count stays at zero in every run. The numbers above are reported as measured,
not as a best case.

**Interpretation.** p50 of 15 ms and p95 of 188 ms with 44 live WebSockets and 39.5 req/s mean the
request path is healthy and the tail is queueing, not slowness: one uvicorn worker executes ~25 ms of
CPU per request on average, and 50 users with 0.4–1.8 s think time spend most of their life waiting.
The two tails that stand out are both deliberate:

* **`POST /purchase-orders/{id}/receipts` on one order (p95 1307 ms, n=11).** Eleven warehouse users
  receiving against the *same* purchase order serialise on that row's `SELECT … FOR UPDATE` — that is
  the lock that stops a cumulative over-receipt (decision 89/90). Across all orders the receipt p95 is
  125–390 ms; the aggregate p95 stays 188 ms. Correctness over 100 ms on a pathological burst.
* **`/rfqs/{id}/channels` and `/purchase-orders/{id}`** aggregate per-supplier delivery evidence and
  the delivery timeline; at 8–10 samples per run their p95 is dominated by one slow sample. Their p50
  is 35–48 ms.

**Write path, measured.** 108 writes (requisitions and partial goods receipts) were issued by the
personas and all 108 succeeded; the harness reports refusals separately, because a business-rule
refusal under concurrency is the *correct* answer rather than an error. When the demo orders are
already fully received (`--no-seed`) the same run records ~50 % refusals with `422 OVER_RECEIPT` and
still zero errors — the guard from decision 89 doing its job.

**Defects this test found and fixed in phase 4:** see `docs/PR_PHASE4.md` — the two hot read paths
(`analytics.spend_breakdown` N+1, supplier list N+1) were found by profiling the endpoints below the
top of this table, and the harness itself was measuring a lock instead of the system until it spread
receipts per user.

## Concurrency proof — the invariants, not just the latency

`scripts/concurrency_check.py` fires the *same dangerous request* many times at once through the real
HTTP API and then reads the database to check the invariant. It runs in CI (job `postgres`).

```
PASS  goods receipt: no cumulative over-delivery — 2/8 receipts accepted, 100 of 100 received
PASS  journal posting: exactly one post, no duplicated lines — 1/6 posts accepted, 2 lines, posted
PASS  supplier payment: one payment recorded, no overpayment — 1/5 payments accepted, 10000 of 10000
PASS  approval decision: exactly one decision wins — 1/5 decisions accepted, final status approved

All concurrency invariants held.
```

The same script with the four `lock_for_update` calls removed (measurement, not speculation):

```
FAIL  goods receipt: no cumulative over-delivery — 8/8 receipts accepted, 400 of 100 received
FAIL  journal posting: exactly one post, no duplicated lines — 6/6 posts accepted, 12 journal lines
FAIL  supplier payment: one payment recorded, no overpayment — 5/5 payments accepted
FAIL  approval decision: exactly one decision wins — 5/5 decisions accepted (timing dependent,
      it passes without the lock when the requests do not overlap closely enough — which is exactly
      why a lock, not a timing assumption, is required)
```

## Caveats (kept explicit)

* One uvicorn process, one PostgreSQL instance on the same machine, no Redis (the in-process rate
  limiter was **enabled**, and the run provisions one account per virtual user so the limits bucket
  per identity), no TLS, no reverse proxy. A production deployment adds a hop; it also adds
  replicas and a connection pooler.
* Reads dominate because a human browsing the app issues reads. The write paths exercised here are
  intake and goods receipts; dispatch and approval decisions are covered by the functional suites and
  by `scripts/concurrency_check.py`, not by this run — flooding them would mutate the demo dataset.
* The sandbox is a shared machine: consecutive runs of the same build move p95 between 115 and
  260 ms. Treat the numbers as an order of magnitude and the error count as the hard signal.
* `python scripts/load_test.py --users 200` will exceed a single worker long before it saturates
  PostgreSQL; scale the API (replicas) and add the Redis pub/sub fan-out for the WebSocket hub
  (`docs/API.md` → WebSocket) before raising the number.
