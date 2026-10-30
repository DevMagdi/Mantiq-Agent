# Operations runbook

Everything an operator needs to install, seed, upgrade, monitor, and recover ProcureAI.

## 1. Prerequisites

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11+ | `python -m venv .venv` |
| Node.js | 20+ | frontend build |
| PostgreSQL | 16 | extensions `pgcrypto`, `citext`, `pg_trgm`, `vector` (migration 0001 creates them) |
| Redis | 7 | Celery broker/result backend, rate-limit counter |
| Docker Compose | 2.x | optional, `deploy/docker-compose.yml` |

SQLite works for a laptop or CI (`DATABASE_URL=sqlite+aiosqlite:///./procureai.db`); PostgreSQL is
required for RLS, `pg_trgm` search and pgvector similarity.

## 2. First install (5 minutes)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp ../deploy/.env.example .env          # fill SECRET_KEY + ENCRYPTION_MASTER_KEY at minimum
procureai migrate                        # alembic upgrade head
procureai bootstrap --demo               # reference data + platform superadmin + demo tenant
procureai seed-demo                      # Nile Industries demo dataset
procureai doctor                         # prints env + adapter mode
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm ci
npm run dev -- --host 0.0.0.0 --port 5173
```

The dev server proxies `/api` and `/ws` to `http://127.0.0.1:8000` (`vite.config.ts`).

**Demo credentials** (created by `bootstrap --demo`, password `Demo!Pilot2026`, forced change on
first sign-in for the superadmin):

| Role | E-mail |
| --- | --- |
| Platform superadmin | `admin@pilot.procureai` |
| Procurement manager | `manager@nile.example.com` |
| Buyer | `buyer@nile.example.com` |
| Requester | `requester@nile.example.com` |
| Finance approver | `finance@nile.example.com` |

## 3. Daily operations

| Job | Command | Schedule |
| --- | --- | --- |
| Reminders + RFQ expiry | `celery -A app.workers.celery_app call procureai.rfq_reminders` | every 2h |
| Approval SLA escalation | `... procureai.escalate_approvals` | every 30m |
| Retention purge + erasure | `... procureai.retention_purge` | 02:30 UTC |
| FX snapshot | `... procureai.fx_refresh` | 03:10 UTC |
| Discovery sweep | `... procureai.discovery_sweep` | every 20m |

Start workers:

```bash
celery -A app.workers.celery_app worker --loglevel=info --concurrency=4
celery -A app.workers.celery_app beat --loglevel=info
```

Without Redis the API still serves traffic; run the same jobs from cron with
`python -c "from app.workers.tasks import retention_purge; retention_purge()"`.

## 4. Upgrades

```bash
git pull
pip install -e ".[dev]"          # new deps
alembic upgrade head             # forward-only migrations 0001..0012
alembic check                    # must report "No new upgrade operations detected"
cd ../frontend && npm ci && npm run build
# then restart api + worker + web
```

Rollback: Alembic revisions are reversible (`alembic downgrade -1`), but a downgrade deletes data.
Prefer a forward fix; take a backup first (§6).

## 5. Health & monitoring

| Endpoint | Meaning |
| --- | --- |
| `GET /health` | process is up (no DB access) |
| `GET /ready` | DB round-trip succeeded — use for load-balancer readiness |
| `GET /metrics` | Prometheus text metrics: requests, latency, agent runs, policy blocks |
| `GET /api/docs` | OpenAPI UI (restrict in production) |

Alerts to configure: `policy_block` rate spike, `auth.login_failed` burst, `agent_run.status =
failed`, Celery queue depth, DB connection saturation, p95 latency > 800 ms.

## 6. Backup & restore

* **PostgreSQL**: `pg_dump -Fc procureai > procureai_$(date +%F).dump` nightly; retain 30 days.
* **Encryption keys**: `ENCRYPTION_MASTER_KEY` is *not* in the database. Losing it makes every
  `supplier_contact` record unreadable — store it in the secret manager, not only in `.env`.
* **Object storage**: replicate the bucket holding quotations and PO artefacts.
* **Restore drill**: `pg_restore -d procureai_restore procureai_YYYY-MM-DD.dump`, then run
  `procureai doctor` and a smoke `GET /ready`.

## 7. Incident playbooks

**Agent sends something it should not have.** 1) `POST /rfqs/{id}/policy` with `mode=draft_only`
to stop autonomous sends. 2) Inspect `agent_run` + `tool_call` + `audit_log` for the run id.
3) The message row carries `policy_decision`; if a `BLOCK_*` code is missing, treat it as a policy
bug and open a regression test. 4) Notify the supplier through a human buyer.

**Suspected data leak across tenants.** 1) Rotate `SECRET_KEY` (invalidates sessions) and
`ENCRYPTION_MASTER_KEY` per `docs/SECURITY.md`. 2) Query `audit_log` for the actor and request ids.
3) Confirm RLS is enabled on PostgreSQL (`SELECT relname, relrowsecurity FROM pg_class WHERE
relname IN ('rfq','offer','purchase_order');`). 4) Preserve logs before any cleanup.

**Policy engine blocking legitimate negotiation.** Read the `codes` in the response — each maps to
one rule (`BLOCK_PRICE_ABOVE_WALKWAY`, `BLOCK_ROUND_LIMIT`, `BLOCK_NO_OPT_IN`,
`BLOCK_COMPETITOR_DISCLOSURE`, `BLOCK_AUTO_VALUE_THRESHOLD`, `BLOCK_OUTSIDE_HOURS`). Fix the policy
version, not the block, and record the reason in the approval comment.

**Migrations fail at startup.** The API logs and continues to `/health`; check
`alembic current` vs `alembic heads`, ensure the DB user can create extensions, and re-run
`procureai migrate`. Never edit an applied migration — add a new revision.

**Erasure deadline breach (30 days).** `SELECT * FROM data_erasure_request WHERE status='pending'
AND due_at < now();` → run `POST /admin/erasure-requests/{id}/execute`, then confirm
`privacy.erasure_executed` in `audit_log`.

## 8. Scaling notes

* API and worker are stateless; scale horizontally behind a load balancer (sticky not required,
  WebSocket needs `wss://` pass-through).
* Rate limiting uses Redis when present, so a horizontally scaled fleet shares counters.
* Keep Celery concurrency × pool size below PostgreSQL's `max_connections`.
* Long LLM calls are bounded (`task_time_limit = 600s`); increase only with provider SLAs in hand.

## 9. Sandbox limits (what this repository's CI does *not* prove)

No PostgreSQL, Redis or Docker daemon is available in the build sandbox, so:
migrations run against SQLite, Celery tasks run eagerly, RLS/pgvector paths are dialect-guarded and
untested, and external providers are fakes. `procureai doctor` prints the actual adapter modes, and
`docs/PROGRESS.md` lists the same gaps honestly.
