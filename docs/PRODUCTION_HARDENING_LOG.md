# Production hardening log — ProcureAI (Mantiq-Agent)

This file is the honest, chronological record of the production-hardening programme started on
**2026-11-06**. It lists every command run, its exact result, what was changed, and what is still
missing. Nothing in here is aspirational: if a line says a thing works, the command that proved it
is next to it. If something could not be proven in this environment, it is marked **NOT PROVEN** and
the reason is given.

Environment of this effort:

| Item | Value |
| --- | --- |
| Sandbox host | 2 vCPU, 3.8 GB RAM, Debian 12 (glibc 2.36), Python 3.11.2, Node 22.22.3 |
| Docker daemon | **not available** — images are CI-built, not locally runnable |
| `apt-get` | privilege denied for package installation |
| Outbound network | `github.com`, `codeload.github.com`, `api.github.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org` only. `cdn.playwright.dev`, `playwright.azureedge.net`, `deb.debian.org` → unreachable |
| Real PostgreSQL | **yes**, via the `pgserver` wheel (real PostgreSQL 16.2 binaries, unix socket at `/tmp/pgtest`) |
| Real Redis | **yes**, via the `redislite` wheel (real `redis-server` 6.2.912183 binary) |
| `helm`, `docker`, `psql`, `redis-cli` | not installed |

---

## Phase 0 — Baseline & truth audit (2026-11-06T18:14Z)

### Commands run and exact results

```
backend: python3 -m venv .venv && pip install -e ".[dev]"   OK
backend: pytest tests -o addopts="" --tb=no                789 passed in 61.70s   (docs claimed 746 — STALE)
backend: pytest tests -q --cov=app                         TOTAL 12698 stmts, 1552 miss → 88 % line coverage
backend: ruff check app tests                              All checks passed!
backend: (alembic head not re-run in this phase; see Phase 3 — run against real PG)
frontend: npm ci                                           OK
frontend: npm run typecheck                                OK
frontend: npm run lint                                     OK (zero warnings)
frontend: npm run test                                     89 passed (docs claimed 84 — STALE)
frontend: npm run build                                    OK — index 537.78 kB (140.5 kB gz), charts 407.41 kB (110.0 kB gz)
frontend: npm run e2e:typecheck                            OK (specs compile, strict)
frontend e2e execution                                     NOT EXECUTED — Playwright browser CDN blocked; sparticuz/chromium rescue attempted in Phase 2
python scripts/e2e_journeys.py                             15 journeys PASS in 10.0s (SQLite + bundled real Redis, two replicas, vendor-shaped Twilio/Anthropic HTTP servers)
```

### Stack inventory discovered in the audit

| Fact | Where | Consequence |
| --- | --- | --- |
| Rate limiter is an in-process `defaultdict(deque)` | `backend/app/core/rate_limit.py` | Multi-replica deployments multiply the effective budget by N — **must become Redis-backed** |
| WebSocket hub drops a slow client after the first failed `send_text`; no queue, no metric | `backend/app/api/ws.py` | Needs a bounded per-socket queue + drop metrics |
| `OTEL_ENABLED` documented as "reserved, not wired" | `docs/ENVIRONMENT.md`, `docs/PROGRESS.md` | Needs real OTel spans or the flag must not exist |
| Accounting hidden behind `ACCOUNTING_ENABLED=false` (pilot default) | `app/core/config.py`, `build_api_router(accounting_enabled=…)` | Pilot default ships a procurement platform without its back office; production profile must turn it on |
| `scripts/load_test.py` runs against **one** base URL, single replica | `scripts/load_test.py` | Needs multi-replica mode (round-robin `--base-urls`) |
| `docs/PROGRESS.md` numbers stale (746 tests, 84 frontend tests) | docs | Phase 6 rewrites |
| `billing_helpers.py` keeps an older duplicate privacy implementation | `app/services/billing_helpers.py` | Consolidate into `app/services/privacy.py` |
| `procureai doctor` reports adapter modes but not storage/Redis/limiter/SSO | `app/cli.py` | Extend |
| `/ready` checks DB only | `app/main.py` | Extend to Redis + critical adapters + migration head |
| `live-smoke` CLI command does not exist | — | Create (Phase 1) |
| No `pgserver`-backed verification harness in repo | — | CI keeps its `postgres` service job; local proofs via `pgserver` are recorded in this log, not added as mandatory dev deps |

### Fake / sandbox surfaces still present at baseline

1. `EMAIL_PROVIDER=fake`, `LLM_PROVIDER=fake`, `WHATSAPP_PROVIDER=fake`, `SEARCH_PROVIDER=fake`
   (deterministic fakes) — refused at boot in `staging`/`production` by `app/core/providers.py`
   (`assert_ready`), **except** `AWS_STORAGE_BUCKET` unset silently keeps local-disk storage and
   is **not** part of the strict gate. Phase 1 adds it to the critical set for production.
2. `VOICE_AI_ENABLED` / `VOICE_ASSISTANT_ENABLED` default `false`; the AI voice turn is a scripted,
   deterministic transcript, not a live audio bridge. Documented, stays off by default.
3. Assistant intent classifier is keyword/rule based by design — no LLM in the loop. Stays.
4. `payment_suggestion.execution` is literally `outside_platform`; no PSP adapter exists. Stays.
5. No SSO/OIDC code path exists; `GET /auth/sso/config` reports provider availability only. Phase 4 adds the seam.
6. OTel: nothing emits spans. Phase 4 wires `opentelemetry-*`.
7. Browser E2E never executed (browser binaries unreachable). Phase 2 attempts a registered rescue.

### Baseline verdict

The repository is a well-tested pilot (789 backend tests, 88 % coverage, 15 HTTP-level journeys),
but it is **not** production-ready on 2026-11-06: the rate limiter is per-process, the browser
suite has never run, object storage is not in the production gate, OTel is vaporware, the back
office ships switched off, and every "live vendor" claim ends at a vendor-shaped HTTP server inside
the test process. Phases 1–6 below close each of those, honestly, one phase at a time.

---
