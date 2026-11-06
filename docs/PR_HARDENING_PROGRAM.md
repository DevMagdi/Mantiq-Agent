# Production hardening, for real: audit → vendor gate → browser-verified → multi-replica

Everything on this branch since `dab7b3e`. Nothing here adds a feature to the product: it makes the
existing system survive contact with a real database, a real browser, a real Redis, and a real
operator. Each phase was committed separately; the hardening log carries the full detail and the
honest misses.

## Phase 0 — baseline & exit criteria (`c4b1339`)

`docs/PRODUCTION_HARDENING_LOG.md` opens with a line-by-line audit of what the repo claimed vs what
was proven, the phase plan, and the rules this branch obeys (no mocks on production paths, real
vendor smoke tests, docs match reality, "the LLM proposes, the policy engine disposes" untouched).

## Phase 1 — the vendor gate (`ef8312b`)

No configurable vendor can silently degrade to a fake in staging/production: boot refuses with the
missing settings listed (`ProviderNotConfigured`), and `procureai live-smoke --confirm` performs one
real call per configured vendor and reports PASS/FAIL/SKIP per leg — LLM (shape + tokens), email
(SMTP, optional IMAP receipt proof), WhatsApp, telephony, S3 storage (byte-exact round-trip +
presigned URL), search. `procureai doctor` reports adapter modes and exits non-zero in strict mode.
Local socket-level vendor-shaped tests prove the code paths; **live vendor proof requires accounts
this sandbox does not have** — nothing claims otherwise.

## Phase 2 — browser-verified + Arabic PO PDF (`d3657b3`)

* **Anonymous boot no longer fires three doomed 401s per page load** (`/auth/me`×2 under StrictMode +
  `/auth/refresh` with no cookies). AuthProvider restores only with a refresh cookie, concurrent boots
  share one in-flight restore. Fix in the app, not in the test's tolerances.
* **28/28 Playwright specs pass** against the real stack (PostgreSQL + Redis + API + Vite). CDN-less
  runner included (`npm run e2e:local`, `@sparticuz/chromium`) for hosts where `cdn.playwright.dev`
  is blocked; the specs run in CI through the new `e2e` job (CDN browsers + seeded demo tenant).
* **Arabic PO PDF verified as an image.** PyMuPDF rasterisation showed column overlap in the first
  render; the layout was rebuilt (independent per-column shaped runs, shaped-width trimming, thousands
  separators, real meta block). RTL Arabic reads correctly; Latin/numeric columns do not overlap.
* Also: in-shell 404, spec selectors anchored, `test_env_docs.py` fixed honestly (Phase 1's `.env.example`
  drift went red — recorded, not hidden).

## Phase 3 — multi-replica for real (`1798d43`)

* **Shared rate limiter** — `RATE_LIMIT_BACKEND=redis`: one sliding sorted-set window per identity
  across replicas (atomic pipeline; rejections don't extend lockout); memory default unchanged;
  Redis failure degrades to memory with a logged warning; `doctor` reports the fallback. Proven by
  `tests/test_rate_limit_redis.py` and live (`procureai:rl:*` keys observed).
* **WebSocket back-pressure** — bounded per-socket queue (64 frames) + writer task; a socket 64 frames
  behind is dropped with `1013` instead of damming the room; drops counted and exported on `/metrics`
  as `procureai_ws_slow_disconnects_total`. `tests/test_ws_hub.py`.
* **Tracing is real** — `OTEL_ENABLED=true` auto-instruments FastAPI + SQLAlchemy, OTLP/HTTP export
  (`procureai-api`); broken exporter never breaks boot; proven with an in-memory span exporter
  (`tests/test_observability.py`). Not included: sampling strategy, exemplars, extra scrubbing.

## Verification (executed on this branch, real output)

```
backend   ruff check app tests            All checks passed
backend   pytest                          813 passed
backend   alembic upgrade head + check    clean (0001 → 0017)
frontend  tsc --noEmit / eslint / test    clean / 0 warnings / 89 tests
frontend  npm run build                   ok (6.2s)
frontend  npm run e2e:local               28 passed
over http PO PDF → PyMuPDF rasterise      1 page, legible RTL Arabic, no overlap
live      :8000 + redis                   RATE_LIMIT_BACKEND=redis active; rl keys observed
```

## Honestly still open (not papered over)

* **Live vendor smoke runs** — need Anthropic/OpenAI, Twilio (WhatsApp+voice), SMTP, S3 and SerpAPI/Brave
  accounts; `procureai live-smoke --confirm` is the single gated command for go-live.
* **Multi-replica load measurement** — the limiter is exact across replicas; no 50-user × N-replica
  timing run exists yet. SSO (SAML/OIDC) — absent by design pending a provider. pgvector columns
  exist but no service computes embeddings. Payments stay `outside_platform` (human-executed).
* **Privacy helpers duplicated** (`billing_helpers.py` vs `services/privacy.py`) — consolidation pending.

## Rules kept

No mocks on production paths in staging/production (strict boot + `live-smoke`), no new tool for the
agent that can accept/sign/pay, Arabic RTL + navy/gold untouched, minimal surgical diffs, all existing
suites still green (802 → 813 backend, 89 frontend), secrets never committed.
