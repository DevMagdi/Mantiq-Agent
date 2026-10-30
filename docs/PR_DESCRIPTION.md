# ProcureAI — multi-tenant AI procurement platform

**Branch:** `arena/80427d61-mantiq-agent` → `main`
**Scope:** the complete platform from the master build prompt — backend, frontend, agent, adapters,
seed data, tests, docs, deployment and CI.

> **The LLM proposes, the policy engine disposes.** The agent cannot accept, sign, pay or change
> specifications; a human approval chain is the only path to a purchase order.

---

## 1. What is in this PR

| Area | Contents |
| --- | --- |
| `backend/app` | FastAPI app, 146 REST operations + WebSocket, SQLAlchemy 2 async models, 12-step Alembic chain, agent graph, 12 audited tools, deterministic policy engine, 9 adapter families (real + Fake), Celery worker/beat, Argon2id auth with TOTP MFA, audit log, RBAC, envelope encryption, rate limiting |
| `backend/tests` | 26 files, **464 tests**: unit, service, API, security, agent, guardrails, injection corpus, 150-case extraction harness |
| `frontend/src` | React 18 + Vite + TS strict, 13 pages over 12 routes, Mantiq navy/gold design system, AR-RTL default + EN, dark/light, 4 vitest suites (24 tests) |
| `deploy/` | `Dockerfile.api`, `Dockerfile.worker`, `Dockerfile.web` + nginx, `docker-compose.yml` (Postgres 16 + pgvector, Redis, MinIO, OTel), Helm chart with HPA/PDB/ingress, `.env.example` |
| `.github/workflows/ci.yml` | backend (ruff → alembic → pytest ≥80%), frontend (typecheck/lint/test/build), container builds, `helm lint`/`helm template`, advisory security scans |
| `docs/` | `ARCHITECTURE`, `DECISIONS` (30 ADRs), `TRACEABILITY`, `SECURITY`, `RUNBOOK`, `API`, `DESIGN_SYSTEM`, `AGENT_DESIGN`, `PROGRESS`, this file |

Sizes: 64 app modules / 14 870 lines of backend code, 4 652 lines of tests, 33 frontend sources /
6 855 lines.

## 2. Requirements coverage (see `docs/TRACEABILITY.md` for the full matrix)

* **Intake (FR-IN)** — text/Excel/e-mail/WhatsApp/voice-note ingestion, clarification loop, UNSPSC
  normalisation, submission with completeness gating.
* **RFQ (FR-RFQ)** — drafting, shortlist dispatch per channel, reminders, auto-expiry, kanban stages
  (`draft → dispatched → open → closed`), tokenised no-login supplier form.
* **Extraction (FR-EX)** — per-field provenance and confidence, disagreement detection,
  prompt-injection flagging, human field verification, 150-case harness at ≥95% field accuracy.
* **Comparison (FR-CMP)** — landed cost (freight, duty, FX), TCO, weighted scoring within ±5 % of 1.0,
  matrix, recommendation whose numbers are re-validated against the computed ranking.
* **Negotiation (FR-NEG)** — versioned policy (target/walk-away/rounds/concessions/tone/mode),
  draft → policy decision → human send, send-time revalidation, timeline, competitor-leak scanning.
* **Approvals (AC)** — amount/role-banded chains, sequential steps, reject / revision / delegate /
  escalate / split award, audit trail; only `finalize_award` issues a PO.
* **Suppliers (FR-SUP)** — master data, encrypted contacts, certifications, ratings, discovery runs
  with a human approval gate.
* **Analytics (FR-RPT)** — KPIs, spend by category/supplier, savings by buyer, forecast with stated
  method, Excel export.
* **Non-functional** — 80 % coverage floor enforced in CI, rate limiting, idempotency keys,
  RFC 9457 problem responses, request-id propagation, tenancy isolation asserted by
  `second_tenant` fixtures, WCAG 2.1 AA checklist, `prefers-reduced-motion`.

## 3. Verification — real output from this workspace

### Backend tests + coverage

```
$ cd backend && pytest tests --cov=app
........................................................................ [ 15%]
........................................................................ [ 31%]
........................................................................ [ 46%]
........................................................................ [ 62%]
........................................................................ [ 77%]
........................................................................ [ 93%]
................................                                         [100%]
Name                                Stmts   Miss  Cover   Missing
...
app/services/analytics.py             176      2    99%
app/services/approval.py              187      6    97%
app/services/negotiation.py           141      5    96%
app/services/po.py                     80      3    96%
app/services/seed.py                  122      0   100%
app/services/voice.py                  77      0   100%
app/workers/tasks.py                   89      0   100%
-----------------------------------------------------------------
TOTAL                                8228   1319    84%
464 passed in 41.49s
```

Regression-test inventory (collect-only):

```
tests/test_negotiation_guardrails.py: 122
tests/test_injection_corpus.py: 86
tests/test_api_admin.py: 26
tests/test_numbers.py: 22
tests/test_adapters.py: 18
tests/test_policy_engine.py: 17
tests/test_services_approval_auth.py: 15
tests/test_taxonomy.py: 14
tests/test_api_identity.py: 13
tests/test_services_extraction.py: 12
tests/test_agents_graph.py: 11
tests/test_services_negotiation.py: 11
tests/test_services_privacy_portal.py: 10
tests/test_services_voice.py: 10
tests/test_api_intake.py: 9
tests/test_services_analytics.py: 9
tests/test_services_award_discovery.py: 9
tests/test_services_governance.py: 9
tests/test_api_rfq_lifecycle.py: 8
tests/test_workers.py: 7
tests/test_cli.py: 6
tests/test_public_portal.py: 6
tests/test_api_flow.py: 5
tests/test_extraction_harness.py: 5
tests/test_api_rfq_surface.py: 4
```

### Lint and migrations

```
$ cd backend && ruff check app tests
All checks passed!

$ cd backend && alembic current && alembic check
0012_bootstrap_seed_reference_data (head)
No new upgrade operations detected.
```

### Frontend

```
$ npm run typecheck          # tsc --noEmit
(no output — 0 errors)

$ npm run lint               # eslint . --max-warnings 0
(no output — 0 problems)

$ npm run test
 ✓ src/pages/pages.test.tsx (4 tests)
 ✓ src/components/ui.test.tsx (7 tests)
 ✓ src/lib/format.test.ts (10 tests)
 ✓ src/i18n/i18n.test.ts (3 tests)
 Test Files  4 passed (4)
      Tests  24 passed (24)

$ npm run build
dist/assets/react-PaxvfrL8.js     164.60 kB │ gzip:  53.70 kB
dist/assets/index-2PkswWOU.js     346.41 kB │ gzip:  96.33 kB
dist/assets/charts-1jBeNFW4.js    407.41 kB │ gzip: 110.02 kB
✓ built in 6.82s
```

### Live smoke test (dev servers running)

```
$ curl -s localhost:8000/health
{"status":"ok","app":"ProcureAI","env":"dev","version":"1.0.0"}

$ POST /api/v1/auth/login (buyer@nile.example.com)     -> 200
$ GET  /api/v1/rfqs?page_size=2                        -> 200 {items, total, page}
$ GET  /api/v1/requests?page_size=2                    -> 200 {items, total, page}
$ GET  /api/v1/suppliers?page_size=2                   -> 200 {items, total, page}
$ GET  /api/v1/analytics/kpis                          -> 200 {rfqs_total, offers_total, ...}
$ GET  http://localhost:5173/                          -> 200
```

Full lifecycle (intake → dispatch → reply → extract → compare → policy → negotiate → approve → PO)
is exercised end-to-end by `tests/test_api_flow.py` and `tests/test_api_rfq_lifecycle.py` against the
real app with a real migrated database.

## 4. Safety-critical behaviour (with tests)

| Guarantee | Proof |
| --- | --- |
| Agent cannot accept/sign/pay/change specs — the verbs are absent and an import-time assert refuses them | `tests/test_agents_graph.py` |
| 40-payload prompt-injection corpus cannot change commercial state | `tests/test_injection_corpus.py` (86 cases) |
| Negotiation decisions match an independent policy oracle | `tests/test_negotiation_guardrails.py` (122 cases) |
| Competitor names/prices never leave in a counter-offer | `tests/test_services_negotiation.py` |
| AI voice states it is AI first; recording requires consent; refused consent discards the recording | `tests/test_services_voice.py` |
| No award without a fully approved chain; PO only after human approval | `tests/test_services_governance.py` |
| Auth: Argon2id, lockout, MFA, rotating refresh tokens, RBAC matrix | `tests/test_services_approval_auth.py`, `tests/test_api_identity.py` |

## 5. Decisions worth reviewing

All 30 are in `docs/DECISIONS.md`; the ones a reviewer is most likely to question:

1. **Graph runner instead of LangGraph** — same auditability and human interrupts, fewer moving
   parts (`docs/AGENT_DESIGN.md` §8).
2. **Deterministic policy engine separate from the LLM** — the LLM writes prose and proposes a price;
   `evaluate_outgoing` is the only authority on sending (ADR 12).
3. **SQLite in CI, PostgreSQL in production** — dialect-guarded DDL (citext/pgvector/RLS) with a
   single migration chain that runs on both; limitations are listed in `docs/PROGRESS.md` (ADR 1).
4. **Envelope encryption per tenant** with `key_id` on every ciphertext so data keys can be
   re-wrapped without re-encrypting business rows (`docs/SECURITY.md` §3).
5. **In-process WebSocket hub** — correct for a single replica; multi-replica fan-out is documented
   as the next step rather than pretended (WebSocket section of `docs/API.md`).

## 6. Known limitations (also in `docs/PROGRESS.md`)

* Real SMTP / Meta WhatsApp / Twilio / SerpAPI / Odoo / Stripe / OCR / STT clients are implemented
  but cannot be exercised in this sandbox — only their Fakes run in CI.
* PostgreSQL-only features (RLS enforcement, `pg_trgm` ranking, pgvector similarity) are enabled in
  production and unit-tested at the boundary, not executed here.
* No browser E2E (Playwright) run: the UI is verified by build + typecheck + ESLint + 24 component
  tests.
* `app/agents/llm/providers.py` (23 %) and `app/services/erp_service.py` (43 %) are the least-covered
  modules because their network paths are excluded by design.
* Arabic PO PDFs render but not with the display font — needs an embedded Arabic subset.

## 7. Reviewer checklist

```bash
cd backend && pytest tests --cov=app && ruff check app tests && alembic upgrade head && alembic check
cd frontend && npm ci && npm run typecheck && npm run lint && npm run test && npm run build
# demo data
cd backend && procureai migrate && procureai bootstrap --demo && procureai seed-demo
```

Login `buyer@nile.example.com` / `Demo!Pilot2026` (tenant `nile`).
