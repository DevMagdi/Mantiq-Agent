# Build progress — honest status

Updated: 2026-10-31 (phase 5 — real providers, cross-replica live events, browser-test suite). This
file exists so nobody has to guess what works. Each row says what is implemented, what is proven by a
test, and what genuinely does not exist yet.

Verification commands behind this snapshot. Everything below was executed for this phase; real output
is pasted in `docs/PR_PHASE5.md`.

```bash
cd backend && pytest tests                              # 746 tests, 0 failures, 88 % line coverage
cd backend && ruff check app tests                      # clean
python scripts/e2e_journeys.py                          # 15 journeys PASS (two replicas + real Redis +
                                                        # Twilio-shaped + Anthropic-shaped servers)
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build   # 84 tests, build ok
cd frontend && npm run e2e:typecheck                     # Playwright suite compiles (strict)
```

The PostgreSQL-only proofs (RLS isolation, concurrency invariants, the 50-user load run) were measured
in phase 4 on a real PostgreSQL 16.2 and **were not re-measured in this phase**: the sandbox reset
removed the local server, and no PostgreSQL binary is installable here. They run on every push in the
CI job `postgres`; the numbers below are labelled with the phase they were measured in.

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
| Security | Argon2id, TOTP MFA, rotating refresh sessions, lockout, RBAC matrix, PII decryption fails closed on a master-key mismatch | `tests/test_services_approval_auth.py`, `tests/test_api_identity.py`, `tests/test_crypto_resilience.py` |
| Privacy | export bundle, erasure request workflow, redaction that keeps commercial records | `tests/test_services_privacy_portal.py` |
| Billing | usage metering, invoice generation, usage export | `tests/test_services_award_discovery.py` |
| Admin | users, roles, limits, plants, taxonomy, templates, integrations, API keys, retention, audit | `tests/test_api_admin.py` (26) |
| Integrations (tenant-managed) | **Settings → Integrations** configures `llm` (Anthropic / OpenAI-compatible), `whatsapp` (Meta Cloud API / Twilio), `sms`, `telephony` (Twilio), `email` (real SMTP) and `search` (SerpAPI / Brave) per tenant: per-provider fields, write-only secrets encrypted at rest and masked as `••••last4`, a computed status (`live` / `sandbox` / `not_configured` / `error` / `disabled`), one real vendor call per **Test connection**, `Enable`/`Disable`, and an audit row per change. A complete *and* enabled row outranks the environment for that tenant; disabling hands traffic back to the platform adapter | `tests/test_api_integrations.py` (15, vendor-shaped Twilio/Meta/SerpAPI/SMTP servers), `frontend/src/components/integrations.test.tsx` (8), `scripts/e2e_journeys.py` journeys `integrations` + `integration safety` |
| Workers | five scheduled Celery jobs + beat schedule, run eagerly in tests | `tests/test_workers.py` |
| CLI | migrate, bootstrap, seed-demo, create-tenant, create-user, reset-password, purge, fx-refresh, doctor | `tests/test_cli.py` |
| Frontend | 22 pages across 22 routes, AR-RTL + EN, single navy/gold theme (no light mode, no toggle), collapsible grouped sidebar with a gold active spine, dense tables that scroll inside their card, segmented view/status controls, every page wired to real endpoints, 76 component/interaction/token tests | `frontend/src/**/*.test.ts*`, `npm run build` |
| RBAC gating in the UI | Every route wrapped in `RequirePermission`, settings tabs filtered by permission, dashboard/approvals/activity queries disabled without the read permission, `RestrictedPanel` instead of a 403, supplier balances withheld without `ledger:read` | `frontend/src/components/guard.test.tsx`, `frontend/src/pages/purchaseOrders.test.tsx`, `tests/test_frontend_permissions.py` (UI map ⊆ backend matrix) |
| Design system enforcement | The palette, the single theme and every WCAG AA text pair are asserted against `tokens.css`, so a light-theme media query or a low-contrast tweak fails the build | `frontend/src/styles/tokens.test.ts` (5) |
| Live updates | `/ws` accepted **before** token validation so clients get real close codes (4401/4403), tenant-scoped hub, `type`-keyed frames, JSON or bare `ping` answered with `pong`, 25 s client heartbeat with capped backoff and tab-visibility reconnect | `tests/test_api_ws.py`, `scripts/load_test.py` (44/44 sockets connected under load) |
| PostgreSQL verification | Migration chain `0001→0016` applied from scratch, `alembic check` (no drift), bootstrap + demo seed, 67 RLS policies, append-only refusals, `pg_trgm` indexes and pgvector columns all exercised on a real PostgreSQL 16.2 | `scripts/verify_rls.py` (29/29 PASS locally and in CI), CI job `postgres` |
| Load (phase-4 measurement) | 50 concurrent role-based sessions (reads, requisitions, goods receipts and 44 live WebSockets): 39.5 req/s, p50 15 ms, p95 188 ms, p99 662 ms, 0 errors, 108/108 writes accepted; found and fixed a PII-decryption 500, a hot-loop in the harness and two hot-read N+1s | `scripts/load_test.py`, `docs/LOAD_TEST.md` |
| Concurrency control | The read-modify-write aggregates are locked (`SELECT … FOR UPDATE`) before the check they protect: receipt quantity, invoice payment, journal posting, approval decision. Proven from the outside by firing the same request 5–8× at once | `scripts/concurrency_check.py` (PASS with locks, 400-of-100 received / 6 postings / 5 payments without them), `tests/test_services_receiving.py` (cumulative over-receipt), CI job `postgres` |
| Hot read paths | `analytics.spend_breakdown` and the supplier list batch their lookups instead of querying per row (spend 55 → 5 ms, supplier list 45 → 13 ms) and the dashboard reads the price history once | `tests/test_services_analytics.py`, `tests/test_api_intake.py`, endpoint timings in `docs/LOAD_TEST.md` |
| Post-award screens | PO list with lifecycle filters, PO detail with stepper + receipts + invoice/match tables, receipt/confirm/invoice/approve/payment drawers, supplier balance card | `frontend/src/pages/PurchaseOrdersPage.tsx`, `PurchaseOrderDetailPage.tsx`, `frontend/src/pages/pages.test.tsx` |
| P2P status machine | The eleven stages (requested → in procurement → RFQ sent → offers received → under approval → PO issued → pending receipt → received → confirmed → ready for audit → posted to accounts) derived from the documents, with an append-only transition history and a bilingual rail | `app/services/p2p.py`, `tests/test_services_p2p.py` (17), `tests/test_api_p2p_accounting_e2e.py` |
| Requisition routing | The requesting department creates the need; `POST /requests/{id}/assign` routes it to a named buyer and moves it to `in_procurement` | `app/api/v1/intake.py`, `tests/test_api_p2p_accounting_e2e.py` |
| Auditor document package | Assembled automatically when the requester confirms the delivery: GRN, awarded offer, PO, full approval record, invoice, match result and postings, each SHA-256 hashed and sealed into a manifest that is re-verified on every read | `app/services/audit_package.py`, `tests/test_services_p2p.py`, `tests/test_api_accounting.py` |
| Accounting core | Chart of accounts, cost centres, double-entry journal entries (draft → posted → reversed), manual entries with two-person control, trial balance | `app/services/accounting.py`, `tests/test_services_accounting.py` (24), `tests/test_api_accounting.py` (15) |
| Automatic postings | GR/IR accrual at PO cost on confirmation, invoice posting with GR/IR clearing or direct inventory, VAT input, price variance, AP accrual, payment settlement | `draft_receipt_accrual`, `draft_invoice_posting`, `draft_payment_posting`, `tests/test_services_accounting.py` (exact amounts) |
| Posting controls | `match_not_clean` / `invoice_not_approved` / `audit_review_required` gates, segregation of duties on manual entries, immutable postings, reversal with a mandatory reason | `tests/test_api_p2p_accounting_e2e.py`, `tests/test_api_accounting.py` |
| Cost & analytical accounting | Every inventory/expense debit carries a cost centre, an optional unit number and an item; a `cost_allocation` fact is written on posting; reports by cost centre, item, unit, supplier and order | `accounting.cost_report`, `tests/test_services_accounting.py`, `tests/test_api_accounting.py` |
| Supplier sub-ledger | Invoices, payments and their journal support per supplier with a live payable balance | `accounting.supplier_account`, `GET /accounting/supplier-account/{id}` |
| Accounting screens | Journal list + entry drawer (post/reverse with reason), chart of accounts & cost centres, trial balance / cost analysis / supplier balances, PO accounting panel and audit-package review — all bilingual with permission gating | `frontend/src/pages/JournalPage.tsx`, `AccountingReportsPage.tsx`, `AccountingSetupPage.tsx`, `components/{P2PRail,PostingsCard,AuditPackageCard}.tsx`, `frontend/src/pages/accounting.test.tsx` (6) |
| Phase-2 RBAC | `controller` (draft/post/reverse + cost master data) and `auditor` (read-only money + package review), neither able to approve a purchase; UI matrix mirrored and asserted | `app/core/rbac.py`, `tests/test_frontend_permissions.py` |
| Provider policy (fail-fast) | Every adapter choice goes through one table: in `staging`/`production` a fake on a **critical** path (email, LLM, WhatsApp when enabled, telephony/STT when AI calls are enabled) aborts startup listing the exact variables, and `registry.build`/`default_provider()` raise if code asks anyway. Optional capabilities (SMS, search, OCR, ERP, payments) log a warning instead of blocking a boot. `ALLOW_FAKE_PROVIDERS=true` is the only, explicit, logged way to keep fakes | `app/core/providers.py`, `app/adapters/registry.py`, `tests/test_providers_strict.py` (9) |
| Twilio transport (WhatsApp + SMS + voice) | Real Programmable Messaging/Voice clients: basic auth, form-encoded bodies, `whatsapp:` addressing, `ContentSid`/`ContentVariables` templates, `MediaUrl` documents (an inline upload is refused explicitly instead of being dropped), bounded retries on 429/5xx only, 4xx returned with Twilio's code, voice calls carry the disclosure TwiML first and report `failed` when Twilio rejects them | `app/adapters/twilio.py`, `app/adapters/telephony_ocr_stt.py`, `tests/test_adapters_twilio.py` (15, against a real local HTTP server) |
| Twilio webhooks | Inbound WhatsApp/SMS accept form-encoded callbacks, verify `X-Twilio-Signature` (HMAC-SHA1 over the public URL + sorted params) and refuse unsigned requests with 401; a status callback is mapped to a delivery receipt; SMS is a first-class dispatch channel next to email/WhatsApp | `app/api/v1/rfq.py` (`/inbound/{channel}`), `app/services/messaging.py`, `tests/test_adapters_twilio.py`, `tests/test_api_channels.py` |
| LLM providers | Anthropic Messages API and any OpenAI-compatible `/v1/chat/completions` (OpenAI, vLLM, Ollama, LM Studio): exact env-driven model, forced tool call or `json_object` for structured tasks, retries on 429/5xx/transport only, 4xx fails fast, token metering per tenant | `app/agents/llm/providers.py`, `tests/test_llm_real_path.py` (11, incl. the real pipeline over HTTP) |
| Live events across replicas | One Redis pub/sub channel with a per-process origin id: publish delivers locally first, then to Redis; the listener forwards other replicas' envelopes to its own sockets and skips its own, so no client sees a duplicate. A publish that cannot reach Redis still reaches local sockets and is logged as an incident; `WS_FANOUT_ENABLED=true` in a strict environment refuses to boot without a reachable Redis | `app/services/events.py`, `app/api/ws.py`, `tests/test_ws_fanout.py` (6, against a real `redis-server`), `scripts/e2e_journeys.py` |
| End-to-end journeys (headless) | 15 journeys run against **two** API processes sharing one Redis, a Twilio-shaped server and an Anthropic-shaped server: sign in/refresh/sign out, requisition → RFQ → dispatch, outbound WhatsApp accepted by Twilio, signed inbound webhook (and 401 without a signature), the resulting `offer.extracted` frame arriving on **both** replicas, a supplier reply extracted by the real LLM provider, capabilities and a feature flag refusing a send, a tenant's real Anthropic + Twilio credentials saved on the Integrations screen flipping both adapters `sandbox` → `live`, and disabling one proving no vendor call happens until it is re-enabled | `scripts/e2e_journeys.py`, CI job `backend` |
| Template authoring | The template library now creates new versions (code, channel, locale, subject, body, variables) through `POST /templates`, permission-gated in the UI, with the previous version deactivated by the backend | `frontend/src/pages/TemplatesPage.tsx`, `frontend/src/pages/channels.test.tsx` |
| Environment reference | Every variable the process reads, what it changes, and what fails without it | `docs/ENVIRONMENT.md`, `deploy/.env.example` |
| Channel selection | Per-supplier preferred + secondary channel, per-RFQ override (with a reason), a resolver that ranks WhatsApp → voice → email → web form and reports *why* each candidate was rejected, mixed-channel RFQs | `app/services/channels.py`, `tests/test_services_channels.py` (12), `tests/test_api_channels.py` (18), `frontend/src/components/{RfqChannelPanel,SupplierChannelsPanel}.tsx` |
| WhatsApp (first-class) | Text, approved templates, documents and interactive structured quote requests; inbound text/audio/document ingestion with STT and OCR → the normal extraction + verification path; delivery/read receipts that never move backwards; per-channel consent with STOP/START in Arabic and English honoured before any parsing; searchable history per supplier and per RFQ | `app/adapters/whatsapp.py`, `app/services/whatsapp_channel.py`, `tests/test_services_whatsapp.py` (11) |
| Voice (human + AI) | Outbound calls linked to RFQ + supplier, disclosure first, recording only on explicit consent, country calling windows, full transcript and per-turn audit | `app/services/voice.py`, `app/services/voice_agent.py`, `tests/test_services_voice.py`, `tests/test_services_voice_agent.py` (9) |
| AI voice agent | Deterministic policy-first agent: classifies every supplier utterance, answers only from approved RFQ data (price validity, delivery, payment terms, read-only specs), blocks competitor disclosure / commitments / award intent / spec changes / payment execution with a fixed refusal and a `block_code`, then feeds the transcript into the same human verification queue | `voice_agent.run_ai_call`, `tests/test_services_voice_agent.py` (9), `frontend/src/pages/VoiceCallsPage.tsx` |
| Internal assistant | Read-only assistant for requester/buyer/manager/warehouse/finance: PR drafting (the only write, and it lands unsubmitted), P2P document status, offer/recommendation summaries, outstanding balances, supplier communication history — every intent declares the permission it needs, ownership is checked after permission, approve/award/sign/post phrases are refused, every turn is stored and audited | `app/services/assistant.py`, `tests/test_services_assistant.py` (11), `frontend/src/pages/AssistantPage.tsx` |
| Unified inbox | One thread per (RFQ, supplier) showing every channel, the resolved reply channel, communication status, messages + events, search, and a reply that always lands on the right channel (`switched_from` when it differs) | `app/services/inbox.py`, `tests/test_services_inbox.py` (7), `frontend/src/pages/InboxPage.tsx` |
| Templates & reminders | 20 bilingual rows per tenant (WhatsApp, email, voice scripts) with `kind`, description, extracted variables and an editable library; reminders and missing-information follow-ups resolve the supplier's channel and language instead of hardcoding email | `app/services/catalog_data.py`, `app/services/messaging.py`, `tests/test_services_reminders_templates.py` (8), `frontend/src/pages/TemplatesPage.tsx` |
| Feature flags | Deployment switch **and** per-tenant flag, resolved in one place, unknown keys rejected, every gated endpoint failing closed with `BLOCK_FEATURE_OFF`; admin tab to toggle them with bilingual labels | `app/services/features.py`, `tests/test_api_channels.py::test_every_gated_endpoint_fails_closed_when_its_flag_is_off`, `frontend/src/pages/SettingsPage.tsx` |
| Phase-3 RBAC | `channel:read/write`, `inbox:read/reply`, `voice:call`, `assistant:use` added to the matrix and mirrored in the UI; the assistant's own intent→permission map is asserted against it | `app/core/rbac.py`, `tests/test_frontend_permissions.py`, `frontend/src/lib/permissions.ts` |

| Phase-4 acceptance cycle | One test walks the whole chain for real: request (text) → clarifications → routed to procurement → RFQ on WhatsApp + email + voice with a simulated inbound WhatsApp reply, email reply and AI voice transcript → extraction + human verification → comparison + negotiation → banded approval chain → PO issued → **partial** receipt → requester confirmation → sealed auditor package → 3-way match → journal entries drafted and posted → supplier ledger moves → costs on the product, cost centre and unit → payment suggestion with **no payment executed**. A second test walks the failure paths (unverified offer, mismatch without an override reason) and a third sweeps every persona's permissions | `tests/test_api_phase4_full_cycle.py` (5 tests) |
| Audit completeness | Step 16 of the acceptance cycle asserts one audit row per transition of the cycle: `intake.text`, `pr.submitted`, `rfq.created`, `rfq.dispatched`, `extraction.verified`, `negotiation.policy_set`, `approval.requested`, `approval.approve`, `po.issued`, `goods_receipt.created`, `goods_receipt.confirmed`, `invoice.created`, `invoice.approved`, `payment.recorded`, `journal_entry.posted`, `audit_package.assembled`, `audit_package.accepted` | `tests/test_api_phase4_full_cycle.py`, `app/services/po.py` (`po.issued` was missing and was added in this phase) |

## Partial — real code, but one leg needs infrastructure that is not in this sandbox

| Item | What is missing | Consequence |
| --- | --- | --- |
| pgvector similarity ranking | The `embedding_vec vector(1536)` columns and HNSW indexes are created by migration `0011` (verified on real PostgreSQL 16), but no service computes embeddings yet | Ranking falls back to the trigram/keyword path; the columns are ready for an embedding provider |
| Real adapters | SMTP, Meta WhatsApp Cloud API, SerpAPI/Brave, Odoo, Stripe, tesseract/Whisper | Interfaces and clients are implemented and unit-tested against local servers; no live vendor account was available in this sandbox, so no call reached the vendor's own service |
| Live vendor credentials | A Twilio account SID/token with a WhatsApp sender, and an Anthropic/OpenAI key | Everything except the vendor's own endpoint is proven locally; a live smoke test needs the credentials and outbound egress listed in `docs/ENVIRONMENT.md` |
| Twilio at scale | WhatsApp template approval, media hosting for documents | Documents require a public `MediaUrl` (the adapter refuses rather than dropping them); templates need Meta approval of the Content template |
| Object storage | S3/MinIO upload path | `LocalStorage` is the working default; the S3 client is behind credentials |
| Browser E2E | A Playwright suite (`frontend/e2e`) covering sign-in/out, RTL/English, every route and the WebSocket handshake exists and typechecks, but **no browser binary can be downloaded in this sandbox** (the CDN is blocked), so it has not been executed | The same journeys are proven without a browser by `scripts/e2e_journeys.py` (HTTP + WebSocket, two replicas); a machine with network access runs `npx playwright install chromium && npm run e2e` |
| WebSocket back-pressure | A slow client is dropped after its first failed send; there is no per-socket queue | Load shedding is deliberate (a stuck browser must not stall a replica), but a burst to a slow client loses intermediate frames |
| Real payment execution | Bank/PSP integration | Deliberately absent: the product records payments made outside it. `payment_suggestion.execution` is literally `outside_platform`, and there is no adapter seam to enable by accident |
| WhatsApp production transport | A real Meta Cloud API / BSP account (phone number id, permanent token, approved template names) | `WhatsAppProvider` is implemented against the documented Cloud API shapes; CI runs the deterministic fake, so retries, receipts and rich messages are proven but not against Meta's live service |
| Voice media (STT/TTS duplex) | A telephony provider with media streams | The AI agent plays a scripted transcript through the `TelephonyProvider` seam and stores every turn; there is no live audio bridge, so "the AI speaks" is a scripted, audited turn, not a live synthesis |
| Assistant natural language | Nothing — by design the intent classifier is keyword/rule based (`assistant.classify`), the answers are assembled from approved data, and no LLM is in the loop | A wider variety of phrasings falls back to `help`; the upside is that the RBAC and forbidden-action guarantees cannot be prompt-injected away |
| AI voice free-form dialogue | Deliberately absent | Same reason as above: `voice_agent.classify_question` is deterministic, so a supplier cannot talk the agent past the guardrail. The approved answer set is `approved-data-v1` |

## Not implemented (deliberate, documented, no fake screens)

| Item | Why it is absent | Where the extension point is |
| --- | --- | --- |
| SSO (SAML/OIDC) | No identity provider in scope; a fake login would be dishonest | `GET /auth/sso/config` reports provider availability from env; `app/services/auth.py` is the seam |
| Fully automated key rotation CLI | Needs a running secret manager | `app/core/crypto.py` records `key_id` so DEKs can be re-wrapped; procedure in `docs/SECURITY.md` |
| Mobile app | Not in scope | The API is versioned and CORS-configured for a future client |
| e-Invoicing / VAT submission | Country-specific compliance not in the SRS extract | Invoice model carries `lines`, `currency`, `status`; a submission adapter would slot in next to the payment adapter |
| Distributed tracing (OTel) | `deploy/otel-collector.yaml` exists but the application does not emit spans or metrics yet; `OTEL_ENABLED` is reserved | Add `opentelemetry-instrumentation-fastapi` + an OTLP exporter in `app/main.py`; request ids and structured logs already carry the correlation id |

## Infrastructure verified in CI

All three images build (`docker build` in the container job), `helm lint` and `helm template` pass,
`pip install -e ".[dev]"` works from an empty virtualenv, and the whole test suite runs on
GitHub-hosted runners. A dedicated CI job migrates a real PostgreSQL 16 (`pgvector/pgvector:pg16`)
from scratch, runs `alembic check`, proves RLS isolation and (new in this phase) runs the concurrency
proof `scripts/concurrency_check.py` against the live API.
The compose file itself is only YAML-validated locally — no Docker daemon ran it. Redis, MinIO and
the Celery workers are not executed here; their code paths run eagerly in the test suite.

## Known rough edges

1. `app/services/billing_helpers.py` still contains an older privacy implementation
   (`export_subject_data`, `execute_erasure`) kept for the CLI `purge` path; the API uses the newer
   `app/services/privacy.py`. Both are tested; consolidating them is a clean-up task, not a
   behaviour gap.
2. `app/agents/llm/providers.py` is now exercised over a real socket (success, retry, 4xx, transport
   failure) but still not against a vendor's live endpoint; a live smoke test needs `LLM_API_KEY`.
3. The WebSocket hub has no back-pressure: a slow client is dropped after the first failed send.
4. Arabic PDF rendering of purchase orders uses the Latin font stack; Arabic strings render but not
   with the display face. Requires an embedded Arabic font subset.
5. Frontend bundle: 400 kB (108 kB gzipped) for the app shell plus a 407 kB (110 kB gzipped) charts
   chunk. Vendor/tables/drag are split, but the analytics page itself is not yet lazily imported and
   the post-award screens ship in the main chunk.
6. `channels.resolve` reads supplier consent one channel at a time; a bulk RFQ dispatch reuses the
   memoised snapshot, so a supplier who opts out *while* a batch is running is honoured on the next
   batch, not mid-batch.
7. The assistant and the AI voice agent are deliberately deterministic (see above): adding an LLM
   later must not move the policy check out of code, which is why `voice_agent` returns
   `knowledge_version` and per-turn `block_code` — a regression there is visible in the data.
8. The load test measures one uvicorn worker against one PostgreSQL instance. Cross-replica behaviour
   is proven separately (`scripts/e2e_journeys.py` runs two API processes over one Redis), but no
   load run has been executed against more than one replica: the rate limiter is still an in-process
   counter, so N replicas multiply the effective per-user budget by N.
9. Concurrent goods receipts against **one** purchase order serialise on that order's row lock by
   design (that is what makes the cumulative over-receipt check safe). Eleven simultaneous receipts
   on the same order reach p95 ≈ 1.3 s while the aggregate p95 stays at 188 ms; a real warehouse
   receives against different orders. Documented rather than "fixed" — removing the lock reopens the
   race that booked 400 of 100 units (`docs/LOAD_TEST.md`).
10. Load-test numbers move ±40 % between runs on this shared sandbox (p95 115–260 ms for the same
   build). The error count and the invariants are the hard signals, not the percentile.
