# Build progress — honest status

Updated: 2026-10-30. This file exists so nobody has to guess what works. Each row says what is
implemented, what is proven by a test, and what genuinely does not exist yet.

Verification commands used for this snapshot (real output in `docs/PR_DESCRIPTION.md`):

```bash
cd backend && pytest tests -q --cov=app        # 513 tests, 0 failures, 85% coverage
cd backend && ruff check app tests             # clean
cd backend && alembic upgrade head && alembic check
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
```

## Implemented and tested

| Area | What works | Proof |
| --- | --- | --- |
| Intake | text / Excel / email / WhatsApp / voice-note ingestion, clarification loop, submission | `tests/test_api_intake.py`, `tests/test_api_rfq_lifecycle.py` |
| Taxonomy | UNSPSC classifier with confidence, human-confirmed normalisation | `tests/test_taxonomy.py` (14) |
| Suppliers | master data, contacts (encrypted), certifications, ratings, discovery + approval gate | `tests/test_adapters.py`, `tests/test_services_award_discovery.py` |
| RFQ | create, dispatch (email/WhatsApp/web form/voice), reminders, auto-expiry, kanban stages, close | `tests/test_api_rfq_surface.py`, `tests/test_workers.py` |
| Public portal | tokenised quote form and supplier self-service page, no login, masked contacts | `tests/test_services_privacy_portal.py`, `tests/test_public_portal.py` |
| Extraction | per-field provenance, confidence, disagreement detection, injection flagging, verification | `tests/test_services_extraction.py`, `tests/test_extraction_harness.py` |
| Comparison | landed cost, duty, FX, TCO, weighted ranking, matrix, recommendation with number guard | `tests/test_services_award_discovery.py` |
| Negotiation | versioned policy, drafting, guardrail decisions, send-time revalidation, timeline | `tests/test_services_negotiation.py`, `tests/test_negotiation_guardrails.py` |
| Approvals | banded chain, sequential steps, reject / revision / delegate / escalate / split award | `tests/test_services_approval_auth.py`, `tests/test_services_governance.py` |
| Purchase orders | numbering, lines, PDF, Excel, ERP export | `tests/test_api_rfq_surface.py` |
| Goods receipts | warehouse receipt with per-line received/accepted/rejected, carrier, delivery note, over-receipt guard | `tests/test_services_receiving.py` (17), `tests/test_api_post_po.py` |
| Delivery sign-off | requester confirmation (quantity/quality/spec/documents), reject with mandatory comment, PO cannot close before it | `tests/test_services_receiving.py`, `tests/test_api_post_po.py` |
| Three-way match | PO ↔ confirmed receipt ↔ invoice, partial receipts and partial invoicing, 8 issue codes, 0.5% per-line price tolerance | `tests/test_services_invoicing.py` (20), `tests/test_api_post_po.py` |
| Invoice approval | human approval only; a mismatch requires a written override reason that is audited | `tests/test_services_invoicing.py`, `tests/test_api_post_po.py` |
| Payment suggestion | deterministic method/terms/priority with reason codes, always `requires_human_approval` and `execution: outside_platform` | `tests/test_services_invoicing.py` |
| Supplier ledger | invoice/payment entries, outstanding and overdue balances per supplier, no overpayment | `tests/test_services_invoicing.py`, `tests/test_api_post_po.py` |
| Analytics | KPIs, spend by category/supplier, savings by buyer, forecast, Excel export | `tests/test_services_analytics.py` |
| Voice | disclosure-first calls, consent-gated recording, country hours, metering, manual script | `tests/test_services_voice.py` |
| Agents | orchestration graph with human interrupts, 12 audited tools, forbidden verbs refused | `tests/test_agents_graph.py` |
| Security | Argon2id, TOTP MFA, rotating refresh sessions, lockout, RBAC matrix | `tests/test_services_approval_auth.py`, `tests/test_api_identity.py` |
| Privacy | export bundle, erasure request workflow, redaction that keeps commercial records | `tests/test_services_privacy_portal.py` |
| Billing | usage metering, invoice generation, usage export | `tests/test_services_award_discovery.py` |
| Admin | users, roles, limits, plants, taxonomy, templates, integrations, API keys, retention, audit | `tests/test_api_admin.py` (26) |
| Workers | five scheduled Celery jobs + beat schedule, run eagerly in tests | `tests/test_workers.py` |
| CLI | migrate, bootstrap, seed-demo, create-tenant, create-user, reset-password, purge, fx-refresh, doctor | `tests/test_cli.py` |
| Frontend | 15 pages across 17 routes, AR-RTL + EN, single navy/gold theme (no light mode, no toggle), collapsible grouped sidebar, every page wired to real endpoints, 27 component/interaction tests | `frontend/src/**/*.test.ts*`, `npm run build` |
| Post-award screens | PO list with lifecycle filters, PO detail with stepper + receipts + invoice/match tables, receipt/confirm/invoice/approve/payment drawers, supplier balance card | `frontend/src/pages/PurchaseOrdersPage.tsx`, `PurchaseOrderDetailPage.tsx`, `frontend/src/pages/pages.test.tsx` |

## Partial — real code, but one leg needs infrastructure that is not in this sandbox

| Item | What is missing | Consequence |
| --- | --- | --- |
| PostgreSQL-only hardening | RLS enforcement, `pg_trgm` search ranking, pgvector similarity | Code paths are dialect-guarded and enabled in production; CI runs SQLite, so they are not exercised here |
| Real adapters | SMTP, Meta WhatsApp Cloud API, Twilio, SerpAPI/Brave, Odoo, Stripe, tesseract/Whisper | Interfaces and clients are implemented; only the deterministic fakes are exercised |
| LLM providers | Anthropic / OpenAI-compatible network calls | Implemented with retries and metering; CI uses `FakeLLM` |
| Object storage | S3/MinIO upload path | `LocalStorage` is the working default; the S3 client is behind credentials |
| Browser E2E | Playwright suite | UI is verified by build + typecheck + ESLint + 27 component tests, not a real browser |
| WebSocket at scale | Redis pub/sub fan-out | The hub is in-process; multi-replica deployments need the fan-out documented in `docs/API.md` |
| Real payment execution | Bank/PSP integration | Deliberately absent: the product records payments made outside it. `payment_suggestion.execution` is literally `outside_platform`, and there is no adapter seam to enable by accident |

## Not implemented (deliberate, documented, no fake screens)

| Item | Why it is absent | Where the extension point is |
| --- | --- | --- |
| SSO (SAML/OIDC) | No identity provider in scope; a fake login would be dishonest | `GET /auth/sso/config` reports provider availability from env; `app/services/auth.py` is the seam |
| Fully automated key rotation CLI | Needs a running secret manager | `app/core/crypto.py` records `key_id` so DEKs can be re-wrapped; procedure in `docs/SECURITY.md` |
| Real duplex voice (STT/TTS loop) | Needs a telephony provider with media streams | `TelephonyProvider` interface in `app/adapters/telephony_ocr_stt.py` |
| Mobile app | Not in scope | The API is versioned and CORS-configured for a future client |
| e-Invoicing / VAT submission | Country-specific compliance not in the SRS extract | Invoice model carries `lines`, `currency`, `status`; a submission adapter would slot in next to the payment adapter |

## Infrastructure verified in CI

All three images build (`docker build` in the container job), `helm lint` and `helm template` pass,
`pip install -e ".[dev]"` works from an empty virtualenv, `alembic upgrade head` + `alembic check`
are clean, and the whole test suite runs on GitHub-hosted runners (Actions run `41098250455`).
The compose file itself is only YAML-validated locally — no Docker daemon ran it.
What cannot be verified here is a live PostgreSQL/Redis/MinIO deployment — the compose stack is
syntax-validated, not executed.

## Known rough edges

1. `app/services/billing_helpers.py` still contains an older privacy implementation
   (`export_subject_data`, `execute_erasure`) kept for the CLI `purge` path; the API uses the newer
   `app/services/privacy.py`. Both are tested; consolidating them is a clean-up task, not a
   behaviour gap.
2. `app/agents/llm/providers.py` is the least-covered module (23%): its network code is excluded
   from CI by design and only the request/response shaping is unit-tested.
3. The WebSocket hub has no back-pressure: a slow client is dropped after the first failed send.
4. Arabic PDF rendering of purchase orders uses the Latin font stack; Arabic strings render but not
   with the display face. Requires an embedded Arabic font subset.
5. Frontend bundle: 394 kB (106 kB gzipped) for the app shell plus a 407 kB (110 kB gzipped) charts
   chunk. Vendor/tables/drag are split, but the analytics page itself is not yet lazily imported and
   the post-award screens ship in the main chunk.
