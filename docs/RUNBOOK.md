# Operations runbook

Everything an operator needs to install, seed, upgrade, monitor, and recover ProcureAI.

## 1. Prerequisites

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11+ | `python -m venv .venv` |
| Node.js | 20+ | frontend build |
| PostgreSQL | 16 | extensions `pgcrypto`, `citext`, `pg_trgm`, `vector` (migration 0001 creates them) |
| Redis | 7 | Celery broker/result backend **and** the WebSocket live-event fan-out (`REDIS_URL`) |
| Docker Compose | 2.x | optional, `deploy/docker-compose.yml` |

SQLite works for a laptop or CI (`DATABASE_URL=sqlite+aiosqlite:///./procureai.db`); PostgreSQL is
required for RLS, `pg_trgm` search and pgvector similarity.

## 2a. Local development on PostgreSQL 16 (required)

SQLite is a sandbox fallback, not a development database: tenant RLS, `pg_trgm` and pgvector simply
do not exist there. Docker is the shortest path:

```bash
docker run -d --name procureai-db -p 5432:5432 \
  -e POSTGRES_USER=procureai -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=procureai \
  pgvector/pgvector:pg16          # postgres 16 + pgvector + contrib (citext, pg_trgm, pgcrypto)

cd backend
export DATABASE_URL="postgresql+asyncpg://procureai:change-me@localhost:5432/procureai"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
alembic upgrade head               # 0001 → 0014: extensions, 60 RLS policies, trgm + HNSW indexes
alembic check                      # must print "No new upgrade operations detected."
python -m app.cli doctor           # connectivity, extensions, migration head, adapter status
uvicorn app.main:app --reload      # seeds reference data + the demo dataset on first boot
```

Without Docker, any PostgreSQL 16 server works, provided the `vector`, `citext`, `pg_trgm` and
`pgcrypto` extensions are installable — on Debian/Ubuntu that is
`apt install postgresql-16 postgresql-contrib-16` plus `pgvector` from your distribution or source.
Then create the role/database and run the same `alembic upgrade head`.

Before going live, check the provider configuration. `APP_ENV=staging|production` refuses to boot
with a fake on a critical path and lists every missing variable in one message; `docs/ENVIRONMENT.md`
documents each one, and `python -m app.cli doctor` prints what the process actually resolved.

```bash
procureai doctor                        # env, adapters, migration head
python scripts/e2e_journeys.py          # 13 real journeys: two replicas + Redis + vendor-shaped servers
```

Verify the guarantees the application relies on:

```bash
export DATABASE_URL="postgresql+asyncpg://procureai:change-me@localhost:5432/procureai"
python ../scripts/verify_rls.py    # 6/6: own rows, foreign rows 0, WITH CHECK, append-only audit
```

If a machine genuinely has no PostgreSQL (an offline sandbox), opt into the degraded mode explicitly
and accept that these guarantees are off:

```bash
export APP_ENV=dev ALLOW_SQLITE_FALLBACK=true
export DATABASE_URL="sqlite+aiosqlite:///./procureai.db"
```

Without that flag the API refuses to start with an explanation naming the variable — this is
deliberate (`docs/DECISIONS.md` §42).

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

## 7a. Trying WhatsApp inbound locally (Meta test number, free tier)

This is the shortest path from "I sent a message" to "a purchase request exists". It assumes uvicorn on
`127.0.0.1:8000` and ngrok; nothing here needs a paid Meta plan.

1. **Meta app + test number.** In the Meta developer console create (or open) an app of type
   *Business*, add the **WhatsApp** product, and use the free **test number** it provisions. Copy the
   *Phone number ID* — that id, not the phone number, is what the platform matches tenancy on.
2. **Register the recipient.** Meta only delivers to numbers added under *To* (up to five on the test
   tier): add your own phone and confirm the code you receive on WhatsApp.
3. **Expose the API.** `ngrok http 8000` → note the `https://…ngrok-free.app` host. The webhook is
   `https://<ngrok-host>/api/v1/inbound/whatsapp`. *(The ngrok inspector at `http://127.0.0.1:4040`
   shows each delivery and its response — the fastest way to see why Meta gave up.)*
4. **Save the credentials in the product.** Sign in as a tenant administrator →
   **Settings → Integrations → WhatsApp**: provider *Meta Cloud API*, the **Phone Number ID** from
   step 1, the **access token**, and an **app secret** and **verify token** you choose. Save, then
   **Test connection** — a green `Live` means the platform really reached the Graph API.
5. **Subscribe the webhook.** In the Meta console → WhatsApp → *Configuration*: callback URL from
   step 3 (Meta calls it with `GET` and expects your **verify token** echoed back — the platform does
   that at `GET /api/v1/inbound/whatsapp`) and subscribe to the **messages** field. Paste the same
   verify token there.
6. **Give a colleague a phone number.** *Settings → Users* → open your user → set the phone in the
   Egyptian form `01xxxxxxxxx` (any of `01…`, `+201…`, `201…` matches; the platform normalises them).
   Then open WhatsApp from that phone, send e.g. *"Need 20 tons of cement, delivery next week"* to the
   test number, and expect a reply with the purchase-request code (or the clarification question).
7. **Replies from suppliers** to an RFQ they were invited to still go down the extraction path: the
   open RFQ invitation decides, not the sender's identity alone.

Things that will bite, in the order they usually do:

* **Signature 401.** Meta signs every delivery with your **app secret** (`X-Hub-Signature-256`). A
  wrong or missing secret gives `401 {"error":"invalid signature"}` and one log line naming the reason
  (`missing_app_secret` / `bad_signature`) — never the secret and never the body. In staging/production
  the app refuses to start with `WHATSAPP_PROVIDER=cloud_api` and no `WHATSAPP_APP_SECRET`.
* **Unknown sender.** A number that matches neither an active user nor a supplier is recorded as
  `received_unmatched` for administrators and gets **no** automatic reply. A `phone_number_id` that no
  tenant registered returns `200 {"processed": 0}` so Meta stops retrying.
* **The 24-hour window.** Outside 24 hours from the recipient's last message, Meta only allows
  *approved templates*, not free text (this is Meta's documented rule, unchanged by this repo). The
  quote-request flow uses templates for that reason; a spontaneous acknowledgement inside the window
  does not need one.
* **Unverified here.** No real Meta account was used in this repository's build sandbox: outbound
  access to `graph.facebook.com` is blocked there, so the provider is exercised against a local server
  that speaks the documented payload shapes (`docs/E2E.md`). The steps above are the ones to run on a
  machine with network access; treat the exact console wording for step 1/5 as unverified against the
  current Meta UI.

### Checking a purchase-order PDF in Arabic

Open any issued PO's PDF (`GET /api/v1/purchase-orders/{id}/pdf`, or the *PDF* button on the order
screen) and confirm four things: the Arabic lines read right to left, the letters are joined
(`حديد` — not `ح د ي د`), Latin and numeric columns are not mirrored, and the text is not boxes. If it
is boxes, the deployment is missing the bundled font or `uharfbuzz`: `python -c "from
app.services.pdf_fonts import arabic_ready; print(arabic_ready())"` prints `False`, and the application
log carries `Arabic PDF font missing` or `shaping is not installed`. Nothing failed silently by design.

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
