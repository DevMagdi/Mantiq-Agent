# Phase 5 — real integrations, no fakes in the production path

Branch: `arena/99846bf6-mantiq-agent` (Arena binds this session to that branch; `main` is never
committed to directly). Base: `1f06786` (merge of PR #8).

Phase 5 was the "make it real" phase: every provider on a critical path now talks to a real vendor
client, the fake adapters survive **only** for tests and for an explicit, loudly-logged escape hatch,
live events reach every replica through Redis, and all of it is proven by a suite that starts real
sockets — a real `redis-server`, real uvicorn replicas, and local servers that speak Twilio's and
Anthropic's/OpenAI's wire formats.

## 1. What changed

### 1.1 Provider policy — a fake can no longer reach a deployment

| File | Change |
| --- | --- |
| `backend/app/core/providers.py` (new) | Declarative `requirements()` table ("if this provider is selected, these variables must exist"), `critical_fake_selections()`, `warnings()`, `problems()`, `assert_ready()`. Single source of truth for the registry, the LLM factory, the startup check and the CLI. |
| `backend/app/main.py` | Lifespan calls `assert_ready()`: `staging`/`production` refuse to start and list every missing variable. `ALLOW_FAKE_PROVIDERS=true` is the only escape hatch, and it logs one warning per faked adapter. |
| `backend/app/adapters/registry.py` | Resolution raises `ProviderNotConfigured` when a real provider is selected without credentials — no silent downgrade at request time. |
| `backend/app/agents/llm/__init__.py` | `default_provider()` builds a real provider from settings; a tenant override outranks the platform default; `fake` is refused under strict mode. |

### 1.2 Twilio — WhatsApp, SMS and voice on one account

| File | Change |
| --- | --- |
| `backend/app/adapters/twilio.py` (new) | Real REST client (`https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json`), Basic auth, form-encoded bodies, `Status`/error mapping, `X-Twilio-Signature` verification (HMAC-SHA1 over the public URL plus the sorted POST parameters). |
| `backend/app/adapters/registry.py` | `whatsapp`/`sms`/`telephony` can all resolve to Twilio; the from-number is chosen per channel. |
| `backend/app/api/v1/rfq.py` | Inbound webhooks: Twilio posts `application/x-www-form-urlencoded`, so the body is read raw, the signature is checked against the URL Twilio signed (`TWILIO_WEBHOOK_URL` when behind a proxy), and only then parsed. The check follows the provider selected **for that channel** (`WHATSAPP_PROVIDER` vs `SMS_PROVIDER`) — reading the wrong one would leave a public endpoint unverified. |
| `backend/app/services/messaging.py` | SMS is a first-class dispatch branch with the same consent, blacklist, policy-hour and bookkeeping rules as WhatsApp and e-mail. |

### 1.3 The real LLM

`backend/app/agents/llm/providers.py`: `AnthropicProvider` (Messages API, `anthropic-version`, forced
tool call for structured tasks) and `OpenAICompatibleProvider` (`/v1/chat/completions`, bearer auth,
`response_format=json_object`) — both covering the self-hosted case (vLLM, Ollama, LM Studio) through
`LLM_BASE_URL`. Rate limits and 5xx/transport errors are retried twice with exponential backoff; 4xx
fails immediately with the vendor's message. `MeteringLLM` records tokens per tenant, and
`GET /offers/extractions` now exposes the model that produced each draft.

### 1.4 Live events across replicas

`backend/app/services/events.py` (new) + `backend/app/api/ws.py`: every event is published to the
Redis channel `procureai:events` in an envelope `{origin, tenant_id, event, payload}`; each process
skips its own envelopes (it already fanned out locally) and forwards everyone else's to its sockets.
`WS_FANOUT_ENABLED` defaults to `true`, and an unreachable Redis stops the API from starting instead
of silently dropping live updates. `GET /ready` reports the bus state.

### 1.5 Message templates — versioned, and editable in the UI

`POST /api/v1/templates` (permission `admin:templates`) bumps `version` for
`(tenant, code, channel, locale)` and deactivates the previous row — a sent message must remain
explainable by the template of its time. `frontend/src/pages/TemplatesPage.tsx` grew a Radix drawer
editor with a real `<label htmlFor>` for every field, a permission-gated submit and a version toast.

### 1.6 Operations: `doctor` became a gate

`backend/app/cli.py::doctor` now reports the resolved database driver, connectivity, migration head
and the PostgreSQL extensions it found, the mode (real/fake) and criticality of every adapter, the
missing credentials, and the live-event transport — **and exits non-zero** when a strict deployment is
still incomplete. It prints no secret value, only whether a variable is set, and it degrades to
`connectivity: failed: …` instead of crashing when the database is unreachable.

Real output (SQLite sandbox, fakes, then the same box pretending to be production):

```text
$ python -m app.cli doctor
{
  "app_env": "dev",
  "strict_providers": false,
  "database": "sqlite+aiosqlite",
  "database_check": {"driver": "sqlite+aiosqlite", "connectivity": "ok",
                     "migration_head": "0016_channels_voice"},
  "ws_fanout_enabled": true,
  "redis_configured": true,
  "note": "Adapters listed under 'fakes' run in sandbox mode with deterministic data."
}
providers: [('whatsapp','fake','fake',True), ('sms','fake','fake',False), ('telephony','fake','fake',True),
            ('llm','fake','fake',True), ('email','fake','fake',True), ('search','fake','fake',False),
            ('stt','fake','fake',True), ('ocr','fake','fake',False), ('erp','file','real',False),
            ('payment','fake','fake',False)]

$ APP_ENV=production python -m app.cli doctor   # fakes still selected
strict_providers: True
critical_fakes: ['WHATSAPP_PROVIDER=fake', 'TELEPHONY_PROVIDER=fake', 'LLM_PROVIDER=fake',
                 'EMAIL_PROVIDER=fake', 'STT_PROVIDER=fake']
$ echo $?
1
```

### 1.7 The environment contract is executable

`docs/ENVIRONMENT.md` documents **all 83** settings (grouped, with required/default/meaning),
`deploy/.env.example` is the operator template, and `backend/tests/test_env_docs.py` fails the build
when a setting is undocumented, when the template declares a variable nothing reads, or when the
template ships something that looks like a live credential. Three documentation errors this test
family caught and this PR fixes: `WS_FANOUT_ENABLED` was documented `false` (code: `true`),
`DEMO_SEED_ON_START` was documented `false` (code: `true`), and `PUBLIC_BASE_URL` had the wrong default.

### 1.8 Two end-to-end layers

| Layer | Entry point | What it proves |
| --- | --- | --- |
| Headless, real stack | `scripts/e2e_journeys.py` | Migrates a database, seeds the demo tenant, starts a real `redis-server` and **two** uvicorn replicas, then drives 13 journeys over HTTP + 2 WebSockets. |
| Browser | `frontend/e2e/*.spec.ts` + `playwright.config.ts` | Sign-in/out, the Arabic↔English switch, all 16 routes + unknown-route 404, and the WebSocket handshake (`connected`/`pong`, bad token → close `4401`). Typechecked in CI. |

See `docs/E2E.md` for prerequisites and why the browser layer is not in CI (the Playwright CDN is
blocked in this sandbox).

## 2. Evidence — commands and their real output

### 2.1 Backend suite

```console
$ cd backend && pytest tests -q --cov=app
...
731 passed, 88 % line coverage      # python -m pytest tests -q --junitxml=: {'tests': '731',
                                    #  'failures': '0', 'errors': '0', 'skipped': '0', 'time': '69.682'}
$ ruff check app tests
All checks passed!
$ ruff check ../scripts/e2e_journeys.py
All checks passed!
```

New suites in this PR: `test_providers_strict.py` (9), `test_adapters_twilio.py` (15),
`test_llm_real_path.py` (11), `test_ws_fanout.py` (6), `test_env_docs.py` (5).

### 2.2 Frontend gates

```console
$ cd frontend && npm run typecheck      # tsc --noEmit                → no output, exit 0
$ npm run lint                          # eslint . --max-warnings 0   → clean
$ npm run test                          # Test Files 13 passed (13) / Tests 76 passed (76) in 20.45s
$ npm run e2e:typecheck                 # tsc --noEmit -p tsconfig.e2e.json → exit 0
$ npm run build                         # ✓ built in 10.03s
```

### 2.3 Headless end-to-end journeys (two replicas + real Redis)

```console
$ python scripts/e2e_journeys.py
ProcureAI end-to-end journeys
• preparing the database (alembic upgrade head → bootstrap → seed demo)
  migrations: ok
  bootstrap: ok
  demo seed: ok
• starting two replicas (127.0.0.1:48621 and 127.0.0.1:56183)
  both replicas are healthy
  PASS  replicas — two API processes share Redis at 127.0.0.1:55327/0
  PASS  sign in — buyer@nile.example.com · 35 permissions
  PASS  token refresh — the single-use refresh cookie mints a new access token
  PASS  sign out — the refresh session is revoked; the access token expires on its own
  PASS  websocket — a live socket is open on each replica
  PASS  requisition — PR-2026-00004 parsed, clarified and submitted
  PASS  RFQ — RFQ-2026-00004 created and dispatched to 1 supplier
  PASS  WhatsApp outbound — Twilio accepted a quote request to whatsapp:+201234567890
  PASS  inbound WhatsApp — unsigned webhook refused (401); signed webhook accepted
  PASS  live event fan-out — offer.extracted reached both replicas' sockets (replica-a, replica-b)
  PASS  LLM extraction — a supplier reply was read by the real provider (['anthropic:claude-sonnet-4-5-20250929'])
  PASS  capabilities — 4 channels, provider=twilio_whatsapp, 6 feature flags
  PASS  feature flag — turning WhatsApp off is enforced server-side, and back on

13 journeys passed in 8.7s
```

### 2.4 API surface

```console
$ python - <<'PY'   # app.openapi()
paths: 178   operations: 207
PY
```

## 3. What is honestly *not* done here

1. **No third-party service was contacted for real.** The Twilio REST endpoint, the Anthropic Messages
   API and the OpenAI-compatible endpoint were replaced by local servers that speak the same wire
   format (`tests/test_adapters_twilio.py`, `tests/test_llm_real_path.py`), because no vendor account
   exists in this sandbox and credentials must not be fabricated. The clients themselves are real:
   real HTTP, real signatures, real retries. Handing over `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/
   `TWILIO_WHATSAPP_FROM` and `LLM_API_KEY` is the only remaining step, and `doctor` will confirm it.
2. **The Playwright suite is written and typechecked, not executed.** `cdn.playwright.dev` is
   unreachable from the sandbox, so no browser binary can be installed. No run is claimed. The same
   journeys are executed without a browser by `scripts/e2e_journeys.py`.
3. **PostgreSQL-only measurements are still the phase-4 ones.** This environment has no PostgreSQL
   with `pgcrypto` (the extension cannot be built here: no OpenSSL headers; a from-source server build
   needs `bison`/`flex`), so RLS 29/29, the p95 latency figure and `alembic upgrade head` on
   PostgreSQL remain the phase-4 numbers, re-measured by CI's `postgres` service on every push.
4. **The rate limiter is per-process** (documented in `docs/DECISIONS.md` #111, not "solved").

## 4. How to verify

```bash
cd backend
python -m app.cli migrate && python -m app.cli bootstrap --demo
python -m app.cli doctor                     # exits 1 if a strict deployment is incomplete
pytest tests -q --cov=app                    # 731 tests, 88 %
ruff check app tests

cd ../frontend
npm ci && npm run typecheck && npm run lint && npm run test && npm run e2e:typecheck && npm run build

cd ..
python scripts/e2e_journeys.py               # 13 journeys, two replicas, real Redis
npx --prefix frontend playwright test        # needs browser binaries (not available in this sandbox)
```

## 5. Reviewer map

| Area | Files |
| --- | --- |
| Provider policy | `backend/app/core/providers.py`, `app/main.py`, `app/adapters/registry.py`, `app/agents/llm/__init__.py` |
| Twilio / SMS | `app/adapters/twilio.py`, `app/adapters/telephony_ocr_stt.py`, `app/services/messaging.py`, `app/api/v1/rfq.py::inbound` |
| LLM | `app/agents/llm/providers.py`, `app/agents/llm/base.py` |
| Live events | `app/services/events.py`, `app/api/ws.py`, `app/db/session.py` |
| Templates | `app/api/v1/rfq.py::create_template`, `frontend/src/pages/TemplatesPage.tsx`, `app/services/messaging.py` |
| Operations | `app/cli.py::doctor`, `docs/ENVIRONMENT.md`, `deploy/.env.example`, `docs/RUNBOOK.md` |
| Proof | `backend/tests/test_{providers_strict,adapters_twilio,llm_real_path,ws_fanout,env_docs}.py`, `scripts/e2e_journeys.py`, `frontend/e2e/` |
| Documentation | `docs/{PROGRESS,DECISIONS,TRACEABILITY,API,RUNBOOK,E2E,ENVIRONMENT}.md`, `README.md` |

New decisions: #97–#113 in `docs/DECISIONS.md` (structured output, retry policy, `LLM_BASE_URL`
semantics, SMS as a channel, tests over real sockets, shipping the browser suite without pretending
to run it, headless journeys, template versioning, `doctor` as a gate, the executable environment
contract).
