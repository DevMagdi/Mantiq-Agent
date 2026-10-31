# Integrations — a screen that actually configures the product

The Integrations tab used to be decorative: it stored a JSON blob that nothing read, so a tenant could
"configure" SMTP and the platform would keep sending with the fake adapter. This change makes the
screen the real control plane for six providers, and proves it end to end.

> **Branch.** Arena binds this session to `arena/cc56b821-mantiq-agent`, so the work is delivered
> from that branch (branch creation and renaming are blocked in this environment); PR #9 was merged
> from the previous session branch `arena/99846bf6-mantiq-agent`, whose tip is this branch's base.

What an administrator can now do without touching `.env`:

1. see every provider's true status — `Live` · `Sandbox` · `Not configured` · `Error` · `Disabled`;
2. type real credentials into clear per-provider fields (never one empty JSON box) and save them;
3. run **Test connection**, which makes one real vendor call and reports the vendor's own answer;
4. enable or disable a provider, which immediately changes what the runtime uses;
5. get a verdict after every action, and find the data + status still there after a reload.

The rule underneath all five: **a complete, enabled tenant configuration always wins**; a disabled or
missing one hands traffic back to the platform adapter instead of silently keeping a fake.

## 1. What changed

### 1.1 One catalogue, one store, one computed status

`backend/app/services/integrations.py` owns a `CATALOGUE` of six tenant-managed kinds and four
platform-only ones. Each entry lists its providers, its config fields (with labels), and which fields
are **secrets**. The screen renders this catalogue, so adding a credential is a backend change and the
UI follows without a frontend release.

| Kind | Providers | Config fields | Secrets |
| --- | --- | --- | --- |
| `llm` | `anthropic`, `openai_compatible` | `model`, `base_url` | `api_key` |
| `whatsapp` | `cloud_api` (Meta), `twilio` | `phone_number_id`, `verify_token`, `app_secret`, `base_url`, `account_sid`, `from_number` | `token`, `auth_token` |
| `sms` | `twilio` | `account_sid`, `from_number` | `auth_token` |
| `telephony` | `twilio` | `account_sid`, `from_number` | `auth_token` |
| `email` | `smtp` | `host`, `port` (587), `user`, `from_address`, `imap_host` | `password` |
| `search` | `serpapi`, `brave` | `base_url` | `api_key` |
| `ocr` · `payment` · `erp` · `stt` | platform-only | shown read-only, never editable per tenant | — |

Status is **computed on every read**, never stored as a wish:

| Status | Meaning |
| --- | --- |
| `live` | complete configuration, enabled, and the last probe succeeded |
| `sandbox` | the platform's fake/platform adapter is in charge |
| `not_configured` | nothing saved and no platform default |
| `error` | saved but incomplete (with the missing field names) or the last probe failed (with the vendor's message) |
| `disabled` | saved, complete, deliberately switched off |

### 1.2 Precedence: tenant → legacy row → platform

`adapter_for()` builds the real adapter from the stored row whenever `configured()` says the
configuration is complete **and** enabled; otherwise it falls back to `registry.resolve(kind)`. The
same call site is used by messaging, WhatsApp inbound/outbound, voice, discovery, RFQ dispatch and the
LLM factory, so there is exactly one precedence chain in the product. `/integrations` reports both
`provider_source` (`tenant` | `legacy` | `platform`) and `platform_provider`, which is how the UI can
say who is in charge instead of guessing.

A changed credential also clears the cached LLM client, so a saved key takes effect on the next
request rather than after a restart.

### 1.3 Secrets are write-only and audited

Secrets are encrypted with the project's existing `EncryptedString`/`encrypt_text` envelope under the
tenant key. The API returns `secrets_saved` (names) and `masked` (`••••` + the last four characters)
and never the value; an empty field on a later save **keeps** the stored secret instead of blanking it.
Every save, enable, disable, delete and probe writes an audit row — `integration.configured`,
`integration.secrets_updated`, `integration.enabled`/`.disabled`, `integration.removed`,
`integration.tested` — naming the changed keys, never their contents.

### 1.4 Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/admin/integrations` | statuses, catalogue, masks, missing fields, platform adapters |
| `PUT` | `/api/v1/admin/integrations/{kind}` | save `{provider, config, secrets, enabled}` |
| `PATCH` | `/api/v1/admin/integrations/{kind}` | `{enabled: false}` hands traffic back |
| `DELETE` | `/api/v1/admin/integrations/{kind}` | remove the tenant row (back to the platform) |
| `POST` | `/api/v1/admin/integrations/{kind}/test` | one real vendor call, with latency and detail |
| `POST` | `/api/v1/admin/integrations` | legacy blob shape, kept for API clients |

`POST .../test` never answers `ok: true` because "the adapter resolved": a fake is `sandbox`/`ok:
false`, an unreachable vendor is `error` with the vendor's own message.

### 1.5 Migration and model

`0017_tenant_integrations` adds `enabled` and `last_error` to `integration_config`; `alembic check`
reports no drift on SQLite and PostgreSQL.

### 1.6 The screen

`frontend/src/components/IntegrationsPanel.tsx` renders one card per kind: a status badge, the source
of the current configuration, per-field inputs (secret fields as masked `password` inputs with a
"leave blank to keep" hint), `Save`, `Test connection` with the vendor answer and latency, and
`Enable`/`Disable`. Every action reports success or the vendor's error. Full Arabic (RTL, default) and
English strings live in `i18n/{ar,en}.ts` under `integrations.*`; the panel is mounted on
**Settings → Integrations**.

## 2. Evidence — commands and their real output

### 2.1 Backend

```console
$ cd backend && ruff check app tests ../scripts/e2e_journeys.py
All checks passed!

$ DATABASE_URL="sqlite+aiosqlite:///./check.db" ALLOW_SQLITE_FALLBACK=true … alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0016_channels_voice -> 0017_tenant_integrations, tenant-managed integrations: enable flag and the last probe error
$ … alembic check
No new upgrade operations detected.

$ python -m pytest tests -q --junitxml=… --cov=app --cov-report=term
junit: {'tests': '746', 'failures': '0', 'errors': '0', 'skipped': '0', 'time': '98.097'}
TOTAL                               12412   1543    88%
```

New suite: `backend/tests/test_api_integrations.py` (15 tests) — an autouse fixture that removes the
tenant's integrations after every test (a saved live config otherwise leaks into 15+ service tests),
plus a vendor-side server that speaks the Twilio account resource, the Meta Cloud API phone-number
resource, SerpAPI and a real ESMTP session.

### 2.2 Frontend

```console
$ cd frontend && npm run typecheck      # tsc --noEmit                      → exit 0
$ npm run lint                          # eslint . --max-warnings 0         → exit 0
$ npm run test                          # Test Files 14 passed (14) / Tests 84 passed (84)
$ npm run e2e:typecheck                 # tsc --noEmit -p tsconfig.e2e.json → exit 0
$ npm run build                         # ✓ built in 6.99s

$ npx vitest run src/components/integrations.test.tsx
 ✓ the integrations screen > renders the catalogue fields instead of an empty JSON box
 ✓ the integrations screen > shows the four honest statuses and the source of each configuration
 ✓ the integrations screen > masks a stored secret and never renders the raw value
 ✓ the integrations screen > saves real credentials through the API and reports the new state
 ✓ the integrations screen > tests a connection and shows the vendor answer with its latency
 ✓ the integrations screen > disables an integration and hands traffic back to the platform
 ✓ the integrations screen > lists platform-managed adapters read-only, without pretending they are editable
 ✓ the integrations screen > speaks Arabic with the same fields when the interface is Arabic
 Test Files  1 passed (1)
      Tests  8 passed (8)
```

### 2.3 The headless journey that starts from the screen

`scripts/e2e_journeys.py` now runs the platform on the fakes and with **no** vendor secret in the
environment:

```
WHATSAPP_PROVIDER=fake  SMS_PROVIDER=fake  TELEPHONY_PROVIDER=fake  LLM_PROVIDER=fake
# TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / WHATSAPP_FROM / LLM_API_KEY / LLM_MODEL : unset
```

so any `live` status or vendor call in that run can only come from credentials `PUT` through
`/integrations/{kind}`. Real output:

```console
$ python scripts/e2e_journeys.py
ProcureAI end-to-end journeys
• preparing the database (alembic upgrade head → bootstrap → seed demo)
  migrations: ok
  bootstrap: ok
  demo seed: ok
  tenant admin: created
• starting two replicas (127.0.0.1:44983 and 127.0.0.1:43801)
  both replicas are healthy
  PASS  replicas — two API processes share Redis at 127.0.0.1:56185/0
  PASS  sign in — buyer@nile.example.com · 35 permissions
  PASS  token refresh — the single-use refresh cookie mints a new access token
  PASS  sign out — the refresh session is revoked; the access token expires on its own
  PASS  websocket — a live socket is open on each replica
  PASS  integrations — fake → live from the screen: llm=live, whatsapp=live, platform still fake
  PASS  requisition — PR-2026-00004 parsed, clarified and submitted
  PASS  RFQ — RFQ-2026-00004 created and dispatched to 1 supplier
  PASS  WhatsApp outbound — Twilio accepted a quote request to whatsapp:+201234567890
  PASS  inbound WhatsApp — unsigned webhook refused (401); signed webhook accepted
  PASS  live event fan-out — offer.extracted reached both replicas' sockets (replica-a, replica-b)
  PASS  LLM extraction — a supplier reply was read by the real provider (['anthropic:claude-sonnet-4-5-20250929'])
  PASS  capabilities — 4 channels, provider=twilio_whatsapp, 6 feature flags
  PASS  feature flag — turning WhatsApp off is enforced server-side, and back on
  PASS  integration safety — disabling falls back without calling the vendor; re-enabling restores it

15 journeys passed in 11.1s
```

The two new journeys assert, in order: `/integrations` starts at `sandbox` for LLM and WhatsApp while
the platform is fake → a tenant admin saves an Anthropic key and Twilio credentials → both become
`live`, the secret is never echoed (only `••••` + last four), both probes really reach the vendor
(the Twilio probe authenticates against `/2010-04-01/Accounts/{sid}.json`, a route the stub serves
with a Basic-auth check that answers Twilio's own `401 20003` when the token is wrong) → a reload still hides the secret → `PATCH enabled:false` turns WhatsApp `disabled` and a
supplier reply is extracted with **no** vendor LLM call and no `anthropic:` model recorded → re-enabling
restores the real provider.

### 2.4 API surface

```console
$ python -c "from app.main import app; s=app.openapi(); print(len(s['paths']), sum(len(v) for v in s['paths'].values()))"
179 210
```

## 3. Acceptance criteria → proof

| Requirement | Where it is proven |
| --- | --- |
| The screen renders all providers with their status | `integrations.test.tsx` (catalogue fields, four statuses, platform-only cards read-only, Arabic parity) |
| Real credentials per provider can be entered and saved | `test_api_integrations.py` (llm, whatsapp, sms, telephony, email, search), `integrations.test.tsx` (PUT body shape `/integrations/llm`) |
| **Test connection** gives an unambiguous result | `test_api_integrations.py` probes against Twilio/Meta/SerpAPI/SMTP servers, `integrations.test.tsx` shows the vendor answer + latency |
| Status flips `fake`/`sandbox` → `Live` after save | e2e `integrations` journey (`llm=live, whatsapp=live, platform still fake`) |
| Reload keeps the data and the status | e2e journey reload step; `snapshot()` is recomputed from the database every call |
| The system really uses the real provider | e2e `LLM extraction` (`anthropic:claude-sonnet-4-5-20250929` recorded on the extraction) and `WhatsApp outbound` (Twilio accepted the message) |
| Disabling restores the safe mode | e2e `integration safety`; `PATCH` → `disabled`, no vendor call, then re-enable restores it |
| Secrets never returned in full, changes audited | `test_api_integrations.py` masking assertions + `integration.*` audit rows |

## 4. What is honestly not done here

* **No live vendor account was contacted.** This sandbox cannot reach `api.anthropic.com`,
  `api.twilio.com`, `graph.facebook.com`, `serpapi.com` or any SMTP relay, and no credentials for them
  exist. Every "real" call above goes to a local server that speaks the vendor's documented wire
  format, with our own client code, authentication headers, retries and error handling executing for
  real. Pointing the same code at production needs only a key in the field — which is the point of the
  screen. If you want a run against your own accounts, supply a test key and I will re-run the probes.
* **No screenshot.** Playwright's browser binary cannot be downloaded here (`cdn.playwright.dev` is
  blocked), so the screen is proven by 8 jsdom component tests plus `frontend/e2e/integrations.spec.ts`
  (written and typechecked in CI, not executed). Nothing in this PR claims a browser run.
* The boot-time provider gate (`assert_ready`) still reads the environment: a process starts before it
  knows which tenants exist, so a strict deployment needs a real `EMAIL_PROVIDER`/`LLM_PROVIDER` even
  if tenants later bring their own keys. Documented in `docs/ENVIRONMENT.md` §2.1.

## 5. How to verify

```bash
cd backend && pytest tests/test_api_integrations.py -q          # 15 integration tests
cd backend && ruff check app tests && alembic check
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
python scripts/e2e_journeys.py                                  # 15 journeys, two replicas, real Redis
```

By hand: sign in as a tenant administrator → **Settings → Integrations** → pick *Language model* →
Anthropic → paste a key → **Save** (badge turns `Live` once the probe passes) → **Test connection**
(shows the model's reply and latency) → reload (the key is `••••abcd`) → **Disable** (badge `Disabled`,
extraction falls back to the deterministic reader).

## 6. Reviewer map

| File | Why |
| --- | --- |
| `backend/app/services/integrations.py` | catalogue, status computation, encryption, save/enable/probe, precedence |
| `backend/app/api/v1/admin.py` (§ integrations) | endpoints + audit rows, legacy shape kept |
| `backend/migrations/versions/0017_tenant_integrations.py` | `enabled`, `last_error` |
| `backend/app/services/{messaging,voice,whatsapp_channel,discovery}.py`, `api/v1/{rfq,channels}.py`, `agents/llm/__init__.py` | every runtime call site now resolves through `adapter_for()` |
| `backend/app/adapters/{twilio,whatsapp,email,search}.py` | `fetch_account`, `verify`, configurable `base_url` for real probes |
| `frontend/src/components/IntegrationsPanel.tsx` | the screen |
| `frontend/src/components/integrations.test.tsx` | 8 component tests |
| `scripts/e2e_journeys.py` | the two journeys that start from the screen |
| `docs/{API,DECISIONS,E2E,ENVIRONMENT,PROGRESS,TRACEABILITY}.md` | contract, rationale, environment, status |
