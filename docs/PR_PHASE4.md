# Phase 4 — final hardening, end-to-end testing and production readiness

Branch: `arena/e1881b79-mantiq-agent` (Arena binds this session to that branch, so the branch named in
the phase-4 brief, `phase-4-final-hardening-and-testing`, could not be created; the work is delivered
from the session branch and merged into `main`).

Phase 4 added **no new feature area**. Everything below is testing, hardening, performance,
correctness and documentation. The one behaviour that grew is refusal: a race that used to corrupt the
books is now refused, and a second decision on an already-decided approval is now an error instead of
a success.

---

## 1. What was tested

### 1.1 The mandatory end-to-end cycle (`tests/test_api_phase4_full_cycle.py`, 5 tests)

One test drives the whole chain through the real HTTP API — no mocks of the services, the fake LLM
provider only:

| Step | What the test actually does |
| --- | --- |
| 1 | Requester states the need (`POST /intake/text`), answers every clarification question, submits; manager routes the requisition to procurement (`POST /requests/{id}/assign` → `in_procurement`) |
| 2–3 | Three suppliers created with three **different preferred channels** (WhatsApp / email / voice), each opted in on its own channel; one RFQ dispatched with `channel: whatsapp` |
| 3 | `GET /rfqs/{id}/channels` shows the **resolved** channel per supplier (WhatsApp, email, voice) — the preferred channel is honoured, not the RFQ default |
| 4a | A **simulated inbound WhatsApp message** (real webhook payload, `X-Hub-Signature-256` path exercised in the provider tests) lands, is threaded, and becomes an extraction |
| 4b | An email reply is ingested through the same inbound pipeline |
| 4c | An **AI voice call** is placed, discloses first, records consent, answers the supplier's question from approved RFQ data only, and the transcript becomes an extraction |
| 5–6 | Field-level human verification, then comparison + negotiation (policy set by the buyer, drafted, sent) |
| 7 | Approval chain requested and stepped through by manager → finance → auditor → admin, each step a named human decision; PO is issued only at the end |
| 8 | **Partial** goods receipt by the warehouse (less than ordered), then a second receipt that completes it |
| 9 | Requester confirms the delivery (quantity/quality/spec/documents), which seals the auditor package |
| 10–11 | 3-way match passes; the auditor package is assembled, hashed, verified and accepted |
| 12 | Controller drafts the GR/IR accrual and the invoice posting and posts both; **GR/IR nets to zero**, inventory is debited exactly once, VAT input is booked, the trial balance stays balanced |
| 13 | Payment is **recorded only** (treasury paid outside the platform), then posted; the supplier sub-ledger's payable falls and a `payment` movement appears |
| 14–15 | Cost lands on the cost centre, the unit and the line item; the lifecycle rail reads `posted_to_accounts`; channel evidence (reply counts per supplier) survives the cycle |
| 16 | The audit trail contains one row per transition: 17 named actions incl. `po.issued`, `goods_receipt.confirmed`, `payment.recorded`, `audit_package.accepted` |

Failure paths in the same module:

* an **unverified offer blocks the comparison** (and the number never reaches the UI);
* an invoice whose quantity exceeds the **confirmed** receipt is **not matched**, cannot be approved
  without a written override reason, and the override is audited;
* a goods receipt that would exceed the order is refused with `422` and `issues[].code == "OVER_RECEIPT"`;
* every persona (requester, buyer, manager, warehouse, finance, controller, auditor, admin) is swept
  against the endpoints it must **not** reach — 403 where expected, 200 where expected, no 500s;
* the AI-voice flags are switched off and the same call is refused cleanly (`BLOCK_FEATURE_OFF`),
  then switched back on;
* a second tenant sees **none** of the conversations, orders or journals.

### 1.2 Concurrency (`scripts/concurrency_check.py`, new — runs in CI)

The load test proves the platform stays fast; this proves it stays **correct**. Each scenario fires
the same dangerous request many times at once through the real API and then reads the database:

```
PASS  goods receipt: no cumulative over-delivery — 2/8 receipts accepted, 100 of 100 received
PASS  journal posting: exactly one post, no duplicated lines — 1/6 posts accepted, 2 journal lines, posted
PASS  supplier payment: one payment recorded, no overpayment — 1/5 payments accepted, paid 10000 of 10000
PASS  approval decision: exactly one decision wins — 1/5 decisions accepted, final status approved

All concurrency invariants held.
```

The same script with the four `lock_for_update` calls removed (measured, not assumed):

```
FAIL  goods receipt: no cumulative over-delivery — 8/8 receipts accepted, 400 of 100 received
FAIL  journal posting: exactly one post, no duplicated lines — 6/6 posts accepted, 12 journal lines
FAIL  supplier payment: one payment recorded, no overpayment — 5/5 payments accepted
```

### 1.3 50 concurrent users (`scripts/load_test.py`)

Freshly migrated + seeded PostgreSQL 16.2, single uvicorn worker, rate limiter **enabled**, 50
provisioned accounts (so limits bucket per identity), 44 live WebSockets, writes included:

```
requests        2823  (39.5 req/s over 71.5 s)
errors          0      (0.00 %)
latency ms      p50 15 | p95 188 | p99 662 | max 1435
status          {'200': 2715, '201': 108}
writes          108 attempted | 108 ok | 0 refused by a business rule
websockets      44/44 connected, 14959 frames, 0 reconnects, 0 failed upgrades
logins          50 ok / 0 failed
------------------------------------------------------------------------
  [PASS] p95 < 800 ms
  [PASS] p99 < 2000 ms
  [PASS] error rate < 1 %
  [PASS] websocket connects 100 %
  [PASS] no 5xx responses
RESULT: PASS
```

Three consecutive runs of the same build: p95 163.6 / 115.3 / 188.2 ms, 0 errors in all three (the
sandbox is a shared machine; the tail moves, the error count does not). Details, per-endpoint tables
and caveats in `docs/LOAD_TEST.md`.

### 1.4 PostgreSQL 16.2, from scratch

```
$ alembic upgrade head        # 0001 → 0016 on an empty database
INFO  [alembic.runtime.migration] Running upgrade 0015_accounting -> 0016_channels_voice, ...
$ alembic current
0016_channels_voice (head)
$ alembic check
No new upgrade operations detected.
$ python -m app.cli bootstrap && python -m app.cli seed-demo
$ python scripts/verify_rls.py
…
All row level security guarantees hold.      # 29/29
```

### 1.5 Backend and frontend gates

```
$ cd backend && pytest tests
672 passed in 55.40s

$ cd backend && pytest tests --cov=app
TOTAL                               11435   1451    87%

$ cd backend && ruff check app tests
All checks passed!

$ cd frontend && npm run typecheck   # tsc --noEmit, strict          → clean
$ cd frontend && npm run lint        # eslint --max-warnings 0       → clean
$ cd frontend && npm run test
 Test Files  13 passed (13)
      Tests  74 passed (74)
$ cd frontend && npm run build
✓ built in 7.00s
```

OpenAPI: **178 paths / 207 operations** (unchanged — phase 4 added no endpoint).

---

## 2. Bugs found and fixed in this phase

| # | Defect | Where | Fix | Proof |
| --- | --- | --- | --- | --- |
| 1 | `GET /purchase-orders` silently ignored a `rfq_id` filter, so the RFQ screen could not jump to its orders | `app/api/v1/orders.py` | the filter is applied | phase-4 E2E (`?rfq_id=`) |
| 2 | **Over-receipt was checked per delivery, not cumulatively**: two deliveries of 60 against an order of 100 both passed | `app/services/receiving.py` | `_validate_lines` compares against `ordered − already booked (received|confirmed)` and the message names what was already received | `tests/test_services_receiving.py::test_two_deliveries_cannot_add_up_to_more_than_was_ordered`, concurrency check |
| 3 | **No row locks** on the read-modify-write aggregates: concurrent requests could double-post a journal entry, pay an invoice twice, receive 400 units of 100, and decide one approval five times | `common.lock_for_update` + `receiving`, `accounting.post_entry`, `invoicing.record_payment`, `approval.decide` | `SELECT … FOR UPDATE` (tenant-scoped, `populate_existing`) before the check it protects | `scripts/concurrency_check.py` — fails without the locks, passes with them |
| 4 | A second decision on an already-decided approval returned **HTTP 200 with `ok: false`**, which looks like success to a double-clicking user | `app/api/v1/rfq.py` | raises `ValidationFailed` (422) like every other refused transition | `tests/test_services_governance.py` (second decision → 422) |
| 5 | Issuing a purchase order left **no `po.issued` audit row** — the transition that spends the money was only implied by the approval row | `app/services/po.py` | explicit audit row with code, amount, currency, supplier, RFQ and split share | phase-4 E2E step 16 |
| 6 | `analytics.spend_breakdown` resolved supplier, RFQ, requisition, plant and category **once per purchase order** (N+1: 55 ms of query time for 12 orders, linear afterwards) | `app/services/analytics.py` | one batched `IN` query per lookup | `tests/test_services_analytics.py`; 55 ms → 5 ms |
| 7 | The supplier list resolved the primary contact and the certifications **once per row** (~50 queries for a 25-row page) | `app/services/suppliers.py` (`supplier_briefs`) | two queries for the whole page, identical payload | supplier tests; 45 ms → 13 ms over HTTP |
| 8 | The dashboard ran the price-history query **twice** (chart + forecast) | `app/services/analytics.py` | one pass shared by both | dashboard 60 ms → 33 ms warm |
| 9 | Rate limiting was bucketed **per IP**, so an office behind one NAT (or 50 load-test users) was throttled as a single client | `app/core/rate_limit.py` | authenticated traffic buckets by `user:<sub>` (240/min), anonymous by `ip:<host>` (600/min), login per account **and** per IP | `tests/test_rate_limit_identity.py` |
| 10 | The load test measured **one row lock**, not the system: every warehouse user received against the same purchase order, and every receipt was then refused because the demo order was already full | `scripts/load_test.py` | context ids resolved per virtual user, five receipt-ready orders seeded (`--receipt-orders`, `--no-seed`) | 108/108 writes accepted in the final run |

Test-only fixes worth naming: the persona sweep used a non-existent supplier id (404 instead of the
permission check), `GET /channels/capabilities` returns `features` as a **list** not a map, and the
`second_tenant` fixture yields a `Tenant` row, not a dict.

---

## 3. Confirmation: Request → Accounting works end to end

`pytest tests/test_api_phase4_full_cycle.py` → **5 passed**, including the golden cycle that ends with:
GR/IR (`110200`) netting to **zero**, inventory (`110100`) debited exactly once at PO cost, VAT input
(`120100`) booked, AP (`210100`) credited, a **balanced** trial balance, a supplier payable that falls
when the payment posting is posted, a `payment` movement in the sub-ledger, cost attributed to the
cost centre `CC-000` and the unit `UNIT-P4`, the rail at `posted_to_accounts`, and a payment
suggestion that exists while **no payment was executed**.

## 4. Confirmation: the safety principles are intact

* No agent tool, stage or LLM call can accept, award, sign, post or pay —
  `tests/test_agents_graph.py::test_post_award_human_actions_have_no_agent_tool` still passes, and the
  12 registered tools are unchanged.
* The payment suggestion still carries `requires_human_approval: true` and
  `execution: "outside_platform"`; there is no payment adapter in the codebase.
* The AI voice agent still answers only from approved RFQ data and refuses competitor disclosure,
  commitments, award intent, spec changes and payment with a recorded `block_code`; the assistant
  still refuses approve/award/sign/pay/post with `can_act: false`.
* Supplier content remains untrusted: inbound WhatsApp/email/voice text reaches an offer only through
  the extraction + human-verification path, unchanged by this phase.
* Phase-4 added locks around **human** actions and made refusals louder; it granted the agents nothing.
  See `docs/AGENT_DESIGN.md` §9 for the re-verification table.

## 5. What is *not* claimed

* No browser-level E2E: the cycle is proven at the HTTP/API level; the UI is proven by typecheck,
  ESLint, 74 component tests and a production build (no browser binary in this sandbox).
* One uvicorn worker, one PostgreSQL instance: this demonstrates correctness and a 50-user workload,
  not horizontal scaling. The WebSocket hub still needs the Redis fan-out for multiple replicas.
* Concurrent receipts against a **single** purchase order serialise on that row's lock by design
  (p95 ≈ 1.3 s for eleven simultaneous receipts on one order; aggregate p95 stays 188 ms). That is
  the correct trade — removing the lock reopens the 400-of-100 race.
* Real providers (Meta, Twilio, Stripe/Odoo, LLM vendors) remain behind credentials and are exercised
  through deterministic fakes, as documented in `docs/PROGRESS.md`.

## 6. Documentation updated in this PR

`docs/PROGRESS.md` (counts, phase-4 rows, rough edges), `docs/TRACEABILITY.md` (phase-4 requirement and
acceptance rows, NFR-CONC-1, NFR-AUD-1, updated NFR-PERF-1), `docs/API.md` (concurrency convention,
rate-limit buckets, cumulative over-receipt contract, `rfq_id` filter, `reason_required`),
`docs/AGENT_DESIGN.md` (§9 safety re-verification), `docs/LOAD_TEST.md` (final numbers, variance,
concurrency proof), `docs/DECISIONS.md` (decisions 89–96).

## 7. How to reproduce

```bash
# PostgreSQL 16 (see docs/RUNBOOK.md §2a), then
cd backend && alembic upgrade head && python -m app.cli bootstrap && python -m app.cli seed-demo
cd backend && pytest tests                       # 672 passed
cd backend && ruff check app tests               # clean
python scripts/verify_rls.py                     # 29/29
python scripts/concurrency_check.py              # 4 invariants hold
python scripts/load_test.py --users 50 --duration 60 --json /tmp/load.json
cd frontend && npm ci && npm run typecheck && npm run lint && npm run test && npm run build
```
