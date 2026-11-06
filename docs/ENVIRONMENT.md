# Environment reference — every variable the application reads

Copy `deploy/.env.example` to `backend/.env`, fill the secrets, and keep the file out of Git.
`python -m app.cli doctor` prints what the running process resolved (and exits non-zero when a strict
deployment is incomplete), while `GET /api/v1/admin/integrations` shows the per-provider state in the UI.

## 1. Core

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `APP_ENV` | yes | `dev` | `dev` \| `test` \| `staging` \| `production`. Drives the provider policy below. |
| `SECRET_KEY` | yes | — | JWT / signing secret, ≥ 32 chars. |
| `ENCRYPTION_MASTER_KEY` | yes | — | Fernet key for supplier PII at rest. |
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://…`. SQLite is a sandbox fallback only (`ALLOW_SQLITE_FALLBACK=true`). |
| `PUBLIC_BASE_URL` | yes | `http://localhost:5173` | Used in e-mails, web-form links, media URLs. |
| `REDIS_URL` | prod | empty | Celery broker/backend **and** the WebSocket live-event fan-out channel. Empty means single-replica only. |
| `ALLOW_SQLITE_FALLBACK` | no | `false` | Permits a SQLite database outside the test environment. |

## 2. Provider policy (fail-fast)

`APP_ENV` of `production`/`staging` makes the provider set **strict**: a fake adapter on a critical
path is refused at boot with the exact variable names, and never falls back silently at runtime.

| Variable | Default | Notes |
| --- | --- | --- |
| `ALLOW_FAKE_PROVIDERS` | `false` | Deliberately run a staging box on the deterministic fakes. Logs a warning per provider. |
| `WS_FANOUT_ENABLED` | `true` | Live events are published to Redis and fanned out to every replica's WebSocket clients. Turning it off is a single-replica-only mode; with it on, an unreachable Redis stops the API from starting rather than silently dropping live updates. |

Critical providers (a fake is fatal in `staging`/`production`): `EMAIL_PROVIDER`, `LLM_PROVIDER`
(including `LLM_MODEL` — a model must be named explicitly, never defaulted),
`WHATSAPP_PROVIDER` when `WHATSAPP_ENABLED=true`, `TELEPHONY_PROVIDER` and `STT_PROVIDER` when
`VOICE_AI_ENABLED=true`. **Object storage is also critical**: with `AWS_STORAGE_BUCKET` empty a
strict deployment refuses to boot, because documents would land on an ephemeral local disk.
Everything else (SMS, search, OCR, ERP, payment) is an opt-in capability and only produces a
startup warning.

### 2.1 Runtime integrations (no `.env` edit)

Everything below is the *platform* default. A tenant administrator can also save their own credentials
for `llm`, `whatsapp`, `sms`, `telephony`, `email` and `search` on the **Settings → Integrations**
screen (`PUT /api/v1/admin/integrations/{kind}`); the row is encrypted at rest, masked in the API and
takes precedence over the variables in this document for that tenant only.

Precedence, per request and per tenant: **tenant integration (complete *and* enabled) → legacy
`LlmProviderConfig` row (LLM only) → platform default from `.env`**. Disabling, deleting or
incompleting the tenant row removes it from the chain, so traffic returns to the platform adapter —
which is exactly what "disable" means on that screen.

Two honest limits:

* the boot-time provider gate (`assert_ready`) reads the environment, because a process starts before
  it knows which tenants exist. A strict deployment therefore still needs a real `EMAIL_PROVIDER` and
  `LLM_PROVIDER`, even if every tenant later supplies its own credentials;
* `python -m app.cli doctor` reports the process view. The per-tenant truth is
  `GET /api/v1/admin/integrations`, which lists every provider as `live`, `sandbox`, `not_configured`,
  `error` or `disabled` with the missing field names.

## 3. Messaging providers

### WhatsApp

| Variable | Notes |
| --- | --- |
| `WHATSAPP_PROVIDER` | `fake` (dev/test) \| `twilio` \| `cloud_api` (Meta WhatsApp Cloud API). |
| `WHATSAPP_TOKEN` | `cloud_api` only — permanent system-user token. |
| `WHATSAPP_PHONE_ID` | `cloud_api` only — phone number id. |
| `WHATSAPP_VERIFY_TOKEN` | `cloud_api` only — echoed back by the Meta verification handshake. |
| `WHATSAPP_ENABLED` | Deployment switch for the feature (a tenant flag narrows it further). |

### Twilio (WhatsApp + SMS)

One Twilio account serves both channels. `TELEPHONY_SID`/`TELEPHONY_TOKEN`/`TELEPHONY_FROM` are
accepted as fallbacks for the earliest deployments, but the `TWILIO_*` names win.

| Variable | Required | Notes |
| --- | --- | --- |
| `TWILIO_ACCOUNT_SID` | yes | `AC…` account SID. |
| `TWILIO_AUTH_TOKEN` | yes | Also the webhook signature key. |
| `TWILIO_WHATSAPP_FROM` | for WhatsApp | `whatsapp:+14155238886` (sandbox number) or your approved sender. |
| `TWILIO_SMS_FROM` | for SMS | E.164 sender, e.g. `+12025550123`. |
| `TWILIO_WEBHOOK_URL` | recommended | Public URL of the inbound endpoint. Set it so signature verification uses the exact URL Twilio signed (behind a proxy the request URL may differ). |
| `SMS_PROVIDER` | no | `fake` (default) \| `twilio`. |
| `TELEPHONY_PROVIDER` | no | `fake` (default) \| `twilio` — outbound AI calls. |
| `TWILIO_BASE_URL` | no | Override for a Twilio-compatible gateway; defaults to `https://api.twilio.com`. |

Inbound webhook: `POST /api/v1/inbound/whatsapp` and `POST /api/v1/inbound/sms` accept Twilio's
`application/x-www-form-urlencoded` payload. When `TWILIO_AUTH_TOKEN` is configured the
`X-Twilio-Signature` header is verified and a mismatch is rejected with `401` — an unsigned request
never reaches the extraction pipeline.

### E-mail

| Variable | Required | Notes |
| --- | --- | --- |
| `EMAIL_PROVIDER` | yes in prod | `fake` \| `smtp`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | for SMTP | `SMTP_FROM` is also the default reply-to for supplier threads. |
| `SENTRY_DSN` | no | Error reporting when set. |

## 4. LLM

| Variable | Required | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | yes in prod | `anthropic` \| `openai_compatible` (OpenAI, vLLM, Ollama, LM Studio, any `/v1/chat/completions`) \| `fake` (dev/test). |
| `LLM_MODEL` | yes (strict) | Exact model id, e.g. `claude-sonnet-4-5-20250929` or `gpt-4.1`. Required at boot in staging/production — the startup check refuses to run without one. Never hard-coded. |
| `LLM_API_KEY` | yes (strict) | Anthropic key or bearer token. In strict mode a self-hosted endpoint without auth is not accepted; set any non-empty bearer token instead. |
| `LLM_BASE_URL` | for self-hosted/proxy | Anthropic: a gateway in front of `/v1/messages` (default `https://api.anthropic.com`). OpenAI-compatible: **include the version segment**, e.g. `https://api.openai.com/v1` or `http://vllm:8000/v1`. |

Per-tenant overrides (provider, model, base URL, encrypted key, monthly token budget) are managed in
**Admin → Integrations → LLM** and outrank the platform default. Under strict mode a tenant cannot
select `fake`.

Boundary behaviour: 429/5xx/transport errors are retried twice with exponential backoff (0.5 s, 1 s);
4xx fails immediately with the vendor's message. Structured tasks send a forced tool call (Anthropic)
or `response_format=json_object` (OpenAI) and validate the answer against the task schema.

## 5. Other adapters

| Variable | Values | Notes |
| --- | --- | --- |
| `SEARCH_PROVIDER` + `SEARCH_API_KEY` | `fake` \| `serpapi` \| `brave` | Supplier discovery. |
| `WHATSAPP_APP_SECRET` | Meta app secret | `cloud_api` webhook payload signature. |
| `WHATSAPP_QUOTE_FLOW_ID` | Flow id | Structured quote form; without it an approved template is used. |
| `AWS_STORAGE_BUCKET` (+ `AWS_ENDPOINT`, `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) | empty (dev) \| bucket name | **Critical in staging/production**: empty bucket = local disk = boot failure. S3, Cloudflare R2, GCS (S3-interop) and MinIO are supported; `AWS_ENDPOINT` selects a non-AWS gateway (path-style addressing is used). The bucket must already exist; the application never creates it. |
| `OCR_PROVIDER` | `fake` \| `tesseract` | Scanned quotations (the container image ships `tesseract-ocr` with `ara`). |
| `STT_PROVIDER` | `fake` \| `whisper` | Reuses `LLM_BASE_URL` / `LLM_API_KEY`, i.e. any Whisper-compatible endpoint. |
| `ERP_PROVIDER` | `file` \| `odoo` | Accounting export. |
| `PAYMENT_PROVIDER` + `STRIPE_KEY` | `fake` \| `stripe` \| `paymob` | Recording an intent only: **execution is deliberately outside the platform**. |
| `EMBEDDING_PROVIDER` | `off` \| `openai_compatible` | Supplier similarity. Off keeps lexical / trigram search. `openai_compatible` calls `POST /embeddings` on `EMBEDDING_BASE_URL` (default `LLM_BASE_URL`, else `https://api.openai.com/v1`) with `EMBEDDING_MODEL` (default `text-embedding-3-small`) and `EMBEDDING_API_KEY` (falls back to `LLM_API_KEY`). `EMBEDDING_DIMENSIONS` (default `1536`) must match the model or the `embedding_vec` column is left empty and the JSON copy is still stored. A failed call never invents a vector: search stays lexical. |
| `SSO_ENABLED` | `false` | OpenID Connect sign-in. Also needs `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`. Optional `OIDC_REDIRECT_URI` (default `{PUBLIC_APP_URL}/api/v1/auth/sso/callback`). A tenant opts out with `settings.sso.enabled = false`. SSO never creates a user. |

## 6. Behaviour switches

| Variable | Default | Notes |
| --- | --- | --- |
| `WHATSAPP_ENABLED` | `true` | Deployment-level feature switch; tenant flags can only narrow it. |
| `VOICE_AI_ENABLED`, `VOICE_ASSISTANT_ENABLED` | `false` | Pilot default. The AI-voice surfaces play a scripted, pre-recorded transcript — there is no live audio — so they ship switched off; a tenant flag cannot turn them on against the operator. Set to `true` to restore the phase-3 behaviour. |
| `ACCOUNTING_ENABLED` | `false` | Pilot default. **Deployment-level only, never a tenant toggle**: when `false` the accounting router is not mounted (so `/api/v1/accounting/*`, `/supplier-balances`, `/purchase-orders/{id}/accounting|audit-package|lifecycle` are absent from the API and from `/api/openapi.json`), the accounting permissions are stripped from every caller, no journal is drafted on receipt/invoice/payment, no account or cost centre is seeded, and the auditor package is not assembled. The code, the tables and the migrations are kept: setting it to `true` restores phase-2 behaviour exactly. |
| `RATE_LIMIT_ENABLED` | `true` | 240 req/min per user, 600 anonymous, login 10/account + 120/IP. |
| `CONTACT_HOURS_START` / `CONTACT_HOURS_END` | `7` / `21` | Local hour window in which suppliers may be contacted. |
| `DEMO_SEED_ON_START`, `BOOTSTRAP_ON_START` | `true` | Demo data and automatic migrations at boot. Set both to `false` in production and run `procureai bootstrap` explicitly. |
| `OTEL_ENABLED` | `false` | When `true`, FastAPI and SQLAlchemy emit OTLP/HTTP spans (`procureai-api`). A dead collector logs and does not stop boot. |
| `SENTRY_DSN` | empty | Backend error reporting, when set. |

## 7. Complete index — the remaining variables

Everything below is read from the same `Settings` object, so it is validated and type-coerced at boot
like the ones above. `python -m app.cli doctor` reports the resolved mode of every adapter, the
database connectivity and migration head, the extensions it found, and exits non-zero when a strict
deployment is still missing a credential — it never prints a secret value, only whether it is set.

### 7.1 Application identity, sessions and request limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `ProcureAI` | Shown in e-mails and the OpenAPI title. |
| `API_PREFIX` | `/api/v1` | Mount point of the versioned API. |
| `PUBLIC_APP_URL` | `http://localhost:5173` | Front-end origin used in links inside notifications. |
| `DEFAULT_TIMEZONE` | `Africa/Cairo` | IANA zone for contact windows, SLA clocks and report bucketing. |
| `ACCESS_TOKEN_TTL_MINUTES` | `60` | Access-token lifetime. |
| `REFRESH_TOKEN_TTL_DAYS` | `14` | Refresh-session lifetime (single use, rotated on every refresh). |
| `REQUIRE_MFA` | `false` | Refuse a password-only login when the tenant has MFA enrolled. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowlist; no wildcard is accepted with credentials. |
| `MAX_UPLOAD_MB` | `20` | Hard limit on quotation, OCR and STT uploads. |

### 7.2 Rate limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Per-process limiter in front of every route. |
| `RATE_LIMIT_PER_MINUTE` | `240` | Authenticated requests per minute per user. |
| `RATE_LIMIT_ANONYMOUS_PER_MINUTE` | `600` | Unauthenticated requests per minute per IP. |
| `RATE_LIMIT_LOGIN_PER_ACCOUNT` | `10` | Failed logins per minute per account before the account is throttled. |
| `RATE_LIMIT_LOGIN_PER_IP` | `120` | Failed logins per minute per IP. |
| `RATE_LIMIT_BACKEND` | `memory` | `memory` = per-process buckets (exact for one replica); `redis` = one shared sliding window across replicas (needs `REDIS_URL`); falls back to memory with a logged warning when Redis is unreachable. |

### 7.3 Database pool and isolation

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_POOL_SIZE` | `10` | Async pool size per process. |
| `DB_MAX_OVERFLOW` | `20` | Burst connections above the pool. |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | Recycle a connection after this age (keeps proxies from killing idle sockets). |
| `DB_STATEMENT_TIMEOUT_MS` | `15000` | `statement_timeout` applied on every connection. |
| `TENANT_RLS_ENABLED` | `true` | Row-level security policies per tenant. Only SQLite fallbacks disable enforcement. |

### 7.4 Bootstrap and demo data

| Variable | Default | Meaning |
| --- | --- | --- |
| `BOOTSTRAP_ON_START` | `true` | Run the idempotent bootstrap on application startup. |
| `BOOTSTRAP_DEMO_TENANT` | `true` | Create the demo tenant alongside the platform super-admin. |
| `BOOTSTRAP_SUPERADMIN_EMAIL` | `owner@mantiq.tech` | Platform owner (cross-tenant administration). |
| `BOOTSTRAP_SUPERADMIN_PASSWORD` | empty | Generated and printed once by `procureai bootstrap` when blank. |
| `BOOTSTRAP_ADMIN_EMAIL` | `admin@pilot.procureai` | First tenant administrator. |
| `BOOTSTRAP_ADMIN_PASSWORD` | empty | Generated when blank. |
| `BOOTSTRAP_DEMO_PASSWORD` | `Demo!Pilot2026` | Password for every seeded demo user. Refused in production. |
| `DEMO_SEED_ON_START` | `true` | Seed suppliers, items and historical documents at boot. |

### 7.5 WhatsApp throughput and the contact window

| Variable | Default | Meaning |
| --- | --- | --- |
| `WHATSAPP_DAILY_CAP` | `400` | Maximum outbound WhatsApp messages per tenant per day; the policy engine blocks beyond it. |
| `WHATSAPP_SESSION_WINDOW_HOURS` | `24` | Free-form reply window after the supplier's last message; outside it an approved template is required. |
| `CONTACT_HOURS_START` / `CONTACT_HOURS_END` | `7` / `21` | Tenant-local hours in which an autonomous message may be sent. A human can override. |

### 7.6 Storage

`AWS_STORAGE_BUCKET` unset keeps media on local disk — allowed in dev/test, a **boot failure** in
staging/production. Set it — together with `AWS_ENDPOINT`, `AWS_ACCESS_KEY`, `AWS_SECRET_KEY` — to
use S3, Cloudflare R2, GCS (S3 API interop) or MinIO; the bucket must already exist, the application
never creates it. Non-AWS gateways are contacted with path-style addressing. The adapter exposes
`signed_url()` for expiring, single-purpose object links and `delete()` for retention purges.

## 8. `procureai live-smoke` — the go-live gate

After `doctor` is green, `procureai live-smoke --confirm` makes **one real call per configured
vendor** and reports PASS/FAIL/SKIP per check. It sends real traffic (a real email, a real WhatsApp
message, optionally a real phone call), so it never runs without `--confirm`, and it never prints
secret values.

```bash
procureai live-smoke --confirm \
  --email-to ops@your-company.com --whatsapp-to +201234567890 [--call-to +201234567890]
# subset: procureai live-smoke --confirm --only llm,email
```

| Check | What it does | Receipt proof |
| --- | --- | --- |
| `llm` | One completion against the configured model; asserts response shape and `tokens_in > 0` (the number the billing pipeline records) | vendor response |
| `email` | Sends a marked message over SMTP/STARTTLS | IMAP polling when `LIVE_SMOKE_IMAP_HOST`/`LIVE_SMOKE_IMAP_USER`/`LIVE_SMOKE_IMAP_PASSWORD` are set; otherwise reports "receipt not verified" explicitly |
| `whatsapp` | Sends a session text (or the approved template named in `LIVE_SMOKE_WHATSAPP_TEMPLATE` with `LIVE_SMOKE_WHATSAPP_TEMPLATE_VARS`) to `LIVE_SMOKE_WHATSAPP_TO` | vendor accepts (`provider_message_id`); delivery receipts arrive on the inbound webhook |
| `telephony` | With `VOICE_AI_ENABLED=true`, places a short disclosure-first call to `LIVE_SMOKE_CALL_TO` and polls the call resource until it settles | Twilio call status; a `no-answer` is an honest PASS with the status reported |
| `storage` | PUT → GET → HEAD → presigned-GET (fetched over HTTP) → DELETE on `AWS_STORAGE_BUCKET` | bytes must round-trip exactly |
| `search` | One live query (`LIVE_SMOKE_QUERY`, default "steel suppliers Egypt") | ≥ 1 result returned |

In a strict (staging/production) deployment a check whose vendor is still a fake is a **FAIL**, not a
SKIP — that is the whole point of the gate. Exit code: `0` all selected checks passed, `1` at least
one failed, `2` usage error (including a missing `--confirm`). The command is an operator tool; CI
does not run it because it needs real vendor accounts.

### 7.7 Compose-only variables

These are read by `deploy/docker-compose.yml`, not by the application: `POSTGRES_PASSWORD` (the
database container's password — keep it equal to the password inside `DATABASE_URL`) and
`OTEL_EXPORTER_OTLP_ENDPOINT` (collector address used by the sidecar configuration).
