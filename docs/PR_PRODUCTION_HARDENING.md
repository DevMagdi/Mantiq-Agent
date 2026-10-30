# Production hardening: PostgreSQL-only, verified RLS, gated UI, 50-user load test

Follow-up to the redesign/post-PO merge. No new features — this makes what exists survive contact
with a real deployment: a real database, a real browser socket, a real number of users.

## 1. PostgreSQL 16 is the only supported database (SQLite is an isolated fallback)

* `DATABASE_URL` now defaults to `postgresql+asyncpg://procureai:change-me@localhost:5432/procureai`.
* SQLite starts only with `APP_ENV=test` **or** `ALLOW_SQLITE_FALLBACK=true`. Otherwise `lifespan`
  raises before serving traffic, with the remedy in the message
  (`backend/app/main.py::_assert_supported_database`). The reason is concrete: `tenant_id` RLS,
  `pg_trgm` ranking and pgvector similarity do not exist on SQLite, so a "supported" SQLite install
  silently loses tenant isolation.
* PostgreSQL connections get a real pool (`10 + 20`, recycle 1800 s) and
  `statement_timeout=15000` per connection; the SQLite fallback gets `busy_timeout` +
  `synchronous=NORMAL` instead.
* `metadata.create_all` can no longer mask a failed migration outside dev: on PostgreSQL a failed
  `alembic upgrade` aborts startup (it cannot create RLS policies, extensions or triggers).
* `deploy/.env.example` now ships the PostgreSQL URL, the pool knobs and
  `ALLOW_SQLITE_FALLBACK=false` with an explicit warning about the guarantees it drops.
* `docs/RUNBOOK.md` §2a documents local PostgreSQL 16 (docker one-liner or distro packages),
  `alembic upgrade head`/`check`, `doctor`, and how to opt into the degraded fallback.

### Two PostgreSQL-only defects this uncovered (both fatal, both invisible on SQLite)

| Defect | Symptom on real PostgreSQL | Fix |
| --- | --- | --- |
| `0010` RLS policies cast the tenant GUC to `uuid` while `tenant_id` is `varchar(36)` | `operator does not exist: character varying = uuid` — the chain died on the first policy, so **no** database could ever be created | policies compare `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')` |
| `0012_bootstrap_seed_reference_data` is 33 chars | `value too long for type character varying(32)` on `alembic_version` — the chain died at 0011→0012 | revision renamed to `0012_bootstrap_reference_data`; `migrations/env.py` now asserts the 32-char limit at import |

Plus: `alembic check` was silently drifting because `embedding_vec vector(1536)` and the
`pg_trgm`/HNSW indexes only exist as raw SQL in `0011`. `include_object` now excludes exactly those
seven objects, so `alembic check` is meaningful again.

## 2. RLS is proven, not assumed

`scripts/verify_rls.py` drops into the least-privilege `procureai_app` role and asserts the
guarantees in SQL. Real output on PostgreSQL 16.2:

```
PASS  tenant sees its own suppliers — 39 rows
PASS  another tenant's suppliers are invisible — 0 rows
PASS  cross-tenant move is blocked by WITH CHECK
PASS  another tenant's row cannot be updated — UPDATE 0
PASS  audit_log rejects UPDATE — no grant for the app role
PASS  audit_log rejects DELETE — no grant for the app role

All row level security guarantees hold.      (exit 0)
```

A new CI job (`postgres`) runs on `pgvector/pgvector:pg16`: migrate from scratch → `alembic check` →
assert SQLite is refused outside test/sandbox → run `verify_rls.py`. CI was SQLite-only before,
which is exactly how both defects above survived.

## 3. WebSocket that behaves like a browser socket

* The upgrade is **accepted before** the token is validated, so clients receive a real close code
  (`4401 invalid_token`, `4403 no_tenant`) instead of an opaque handshake failure.
* Frames carry `type` **and** legacy `event` + `payload`; heartbeats are answered for both the
  browser's bare `ping` and a JSON `{"type":"ping"}`.
* `frontend/src/lib/realtime.ts` was rewritten: 25 s heartbeat, capped exponential backoff
  (1/2/4/8/15/30 s + jitter), stops permanently on 4401/4403, reconnects when the tab becomes
  visible, clears every timer on unmount, never logs on `error`.
* Vite now proxies `/api` **and** `/ws` with `ws: true` and `changeOrigin` (plus `/ready`,
  `/metrics`), so the dev/preview origin works without CORS or a second host.

Verified end-to-end through the Vite proxy (`ws://localhost:5173/ws?token=…`):

```
connected: {"type": "connected", "event": "connected", "payload": {"tenant_id": …}}
pong: {"type": "pong", "event": "pong", "payload": {}}
pong(json): {"type": "pong", "event": "pong", "payload": {}}
```

## 4. The UI never asks for something it will be refused

* New `frontend/src/lib/permissions.ts` (mirrors `app/core/rbac.py`) and
  `frontend/src/components/Guard.tsx` (`RequirePermission`, `RestrictedPanel`, `NoAccess`).
* All 11 permission-sensitive routes are wrapped; `/activity` and `/agent-runs` now use
  `audit:read` (they used `report:read`, which a `requester` holds but never needs there).
* Settings tabs are filtered per permission and each tab's query is `enabled:` on that permission,
  so a deep link cannot fire a forbidden request; empty state is `NoAccess`, not a 403.
* Dashboard/approvals/activity queries are `enabled:` on their read permission and render a
  `RestrictedPanel` card instead of 403 noise. A `requester` gets clean empty states.
* `backend/tests/test_frontend_permissions.py` fails the build if the UI references a permission the
  backend does not define, and pins the role expectations (e.g. stores record receipts, the
  requester confirms them, a requester never holds `approval:read`/`audit:read`).

## 5. Clarification UX: the 422 is explained, not thrown

`RequestsPage` now derives `openQuestions`/`canSubmit`, shows a per-question **Waive** path
(`{answer:"", waive:true}`), disables submit with an inline reason, and renders the server's 422 in
an `role="alert"` panel. Every call is `catch`ed through `errorMessage` — no unhandled rejections.
Covered by `frontend/src/pages/requests.test.tsx` (4 tests: blocked submit, enabled submit, waive
payload, 422 surfaced).

## 6. Load: 50 concurrent users, real PostgreSQL, measured

`scripts/load_test.py` — 50 independent sessions (one per demo persona), each with its own login,
role-appropriate endpoint mix, 0.4–1.8 s think time, 10 s ramp, and a live WebSocket for the
personas that hold one. Any 5xx, 429 or 403 fails the run.

```
requests        2956  (41.3 req/s over 71.5s)
errors          0  (0.00 %)
latency ms      p50 10 | p95 82 | p99 317 | max 711
status          {'200': 2956}
websockets      42/42 connected, 42 frames, 0 reconnects
logins          50 ok / 0 failed
  [PASS] p95 < 800 ms     [PASS] p99 < 2000 ms     [PASS] error rate < 1 %
  [PASS] websocket connects 100 %                 [PASS] no 5xx responses
RESULT: PASS
```

Full per-endpoint table and the caveats (single worker, reads dominate, no Redis) are in
`docs/LOAD_TEST.md`.

## Verification (real output, this branch)

```
# backend
$ ruff check app tests
All checks passed!

$ pytest tests -q --cov=app
520 passed in 27.38s        TOTAL 8962 statements, 1332 missed, 85%

$ alembic current   (DATABASE_URL=postgresql+asyncpg://…@localhost:5432/procureai)
0014_drop_theme_preference (head)
$ alembic check
No new upgrade operations detected.

$ python scripts/verify_rls.py       → 6/6 PASS (exit 0)
$ python scripts/load_test.py --users 50 --duration 60   → RESULT: PASS

# frontend
$ npx tsc --noEmit                    → clean
$ npx eslint . --max-warnings 0       → clean
$ npm run test                        → Test Files 6 passed, Tests 34 passed
$ npm run build                       → ✓ built in 6.18s
  dist/assets/index-B06tvgdr.js      399.85 kB │ gzip: 108.25 kB
  dist/assets/charts-1jBeNFw4.js     407.41 kB │ gzip: 110.02 kB
```

Migration chain executed on a real server this time: `0001 → 0014` (71 tables, 4 extensions,
60 RLS policies, 4 trigram indexes, 2 pgvector columns, `procureai_app` role).

## Notes / decisions

* `docs/DECISIONS.md` §42–§49 record the database policy, the two defect fixes, the revision-id
  guard, the autogenerate filter, the CI PostgreSQL job and the load-test contract.
* `docs/PROGRESS.md` now separates what is *verified* from what still needs external
  infrastructure; the pgvector row was corrected to say the columns exist but no service computes
  embeddings yet.
* The demo dataset now seeds on first boot in non-production when `DEMO_SEED_ON_START=true`
  (idempotent), instead of requiring a separate CLI step — the flag existed but did nothing.
