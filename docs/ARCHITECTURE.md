# ProcureAI — Architecture

ProcureAI is a multi-tenant B2B procurement platform built by Mantiq Technologies. A buyer
describes a need in Arabic or English; an agent turns it into a requisition, an RFQ, parsed
supplier offers, a TCO-weighted comparison, a policy-checked negotiation, and — only after a
human approval chain — a purchase order.

The one sentence that explains every design choice here: **the LLM proposes, the policy engine
disposes.** Language models draft text, propose prices and explain rankings. A deterministic,
auditable policy engine decides whether anything may leave the building, and a human decides
whether anything may be awarded.

---

## 1. System shape

```
┌──────────────────────────────┐        ┌──────────────────────────────────────────┐
│ React 18 + Vite + TypeScript │        │ FastAPI (async)                          │
│  Mantiq design system, AR/EN │◀──────▶│  /api/v1/*  REST + /ws realtime          │
│  TanStack Query/Table, dnd-kit│  HTTPS │  Pydantic v2 schemas, problem+json errors│
└──────────────────────────────┘        └───────────────┬──────────────────────────┘
                                                        │ SQLAlchemy 2 (async)
                                    ┌───────────────────┼───────────────────────┐
                                    │                   │                       │
                            ┌───────▼──────┐   ┌────────▼────────┐   ┌──────────▼────────┐
                            │ PostgreSQL 16│   │ Redis + Celery  │   │ Object storage     │
                            │ RLS, pg_trgm │   │ reminders, SLA, │   │ (MinIO in compose) │
                            │ pgvector     │   │ discovery, purge│   │ quotes, POs        │
                            └──────────────┘   └─────────────────┘   └───────────────────┘
                                    │
                    ┌───────────────┴───────────────────────────────────────────────┐
                    │ Adapters (interface + real impl + deterministic fake)          │
                    │ email · WhatsApp · telephony/OCR/STT · search · ERP · FX · pay │
                    └───────────────────────────────────────────────────────────────┘
```

| Layer | Path | Responsibility |
| --- | --- | --- |
| API | `backend/app/api/v1/{identity,intake,rfq,admin}.py` | HTTP surface, RBAC dependency, audit, commit boundary |
| Contracts | `backend/app/api/deps.py`, `core/errors.py` | bearer auth, tenant context, problem+json errors |
| Services | `backend/app/services/*` | all business rules; no HTTP concerns |
| Agents | `backend/app/agents/*` | orchestrator graph, tool registry, guardrails, policy engine, LLM abstraction |
| Data | `backend/app/db/models/entities.py`, `migrations/versions/*` | 60+ tables, one Alembic chain (0001→0012) |
| Adapters | `backend/app/adapters/*` | every external dependency behind an interface with a fake |
| Web | `frontend/src/**` | 15 pages over 17 routes, 11 signature components, AR-RTL default + EN, single navy theme |

## 2. Request lifecycle (the commercial spine)

```
intake (text | Excel | voice note | email | web form)
  → requisition + clarification questions .......... app/services/intake.py
  → normalisation to UNSPSC taxonomy ............... app/services/taxonomy.py
  → RFQ draft + supplier shortlist ................. app/services/rfq.py
  → dispatch over email / WhatsApp / web form ...... app/services/messaging.py
  → replies parsed into extraction + offer ......... app/services/extraction.py
  → human field verification (HITL) ................ app/api/v1/rfq.py /extractions/*
  → landed cost, TCO, weighted ranking ............. app/services/comparison.py
  → recommendation with number guard ............... app/services/comparison.py
  → negotiation policy → counter-offer draft ....... app/services/negotiation.py
  → policy decision (send / keep draft / block) .... app/agents/policy_engine.py
  → approval chain (role + amount bands) ........... app/services/approval.py
  → purchase order + ERP export .................... app/services/po.py
  → savings, cycle time, forecast .................. app/services/analytics.py
```

Each arrow is a service function with its own audit event. Nothing in the spine is a stub: every
stage is reachable from the UI and covered by an integration test (see `docs/TRACEABILITY.md`).

## 3. Multi-tenancy

* **Isolation key.** Every business row carries `tenant_id`. All queries in services take the
  tenant explicitly; the API never trusts a tenant id from the request body.
* **Defence in depth.** Migration `0010_rls_policies_and_grants` installs PostgreSQL row-level
  security policies plus a restricted application role. SQLite (dev/test) skips RLS — the guard
  is `dialect.name == "postgresql"` — so the same code runs against both, with the production
  path hardened.
* **PII per tenant.** Contact names/emails/phones are envelope-encrypted with a per-tenant key
  (`app/core/crypto.py`, `key_id` column), so a database dump does not leak supplier contacts.
* **Cross-tenant probes.** Tests assert a second tenant sees `404` (not `403`) for foreign ids —
  no existence oracle (`tests/test_api_admin.py`, `tests/test_api_identity.py`).

## 4. The agent runtime

* **Fixed tool registry** (`app/agents/tools.py`): twelve tools, each with a Pydantic input model,
  an RBAC permission, a mutating flag and `requires_policy` where relevant. `FORBIDDEN_TOOL_NAMES`
  (accept / sign / pay / edit specs) is asserted absent at import time; calling one returns a
  refusal and writes no state.
* **Every tool call is audited** as a `tool_call` row with arguments, result, latency and status.
  Results are normalised through `json_safe()` before they hit the JSON column — a `Decimal` leak
  would otherwise lose the audit record of a call that really happened.
* **Orchestrator graph** (`app/agents/graph.py`): thirteen stages; `await_replies`, `verify` and
  `approval` are human-interrupt nodes. A stage failure checkpoints (`agent_run.status = failed`),
  never crashes the API. Dispatch is deferred unless a human explicitly sends.
* **Guardrails** (`app/agents/guardrails.py`): untrusted content is fenced
  (`<untrusted_supplier_content>`), invisible-character stripped, injection-scored, and every
  generated justification is number-checked against computed values only.
* **Policy engine** (`app/agents/policy_engine.py`): pure functions over a `PolicySpec` +
  `PolicyContext`, returning `Decision(allowed, action, codes, reasons, warnings)`. Rules: walk-away
  price, round cap, concession allow-list, opt-in per channel, sanctions/blacklist, competitor
  confidentiality, working hours, auto-mode value ceiling, tenant mode ceiling.
* **LLM abstraction** (`app/agents/llm/`): one `LLMClient` protocol with Anthropic (default),
  OpenAI-compatible and a deterministic `FakeLLM`. Tokens and cost are metered per tenant.

## 5. Data model highlights

| Table | Why it exists |
| --- | --- |
| `purchase_requisition` / `pr_line` | the buyer's intent, pre-RFQ, with clarification state |
| `rfq` / `rfq_line` / `rfq_supplier` | the commercial request, per-supplier invitation + token |
| `channel_message` / `message_thread` | every inbound/outbound message, both languages |
| `extraction_result` / `extracted_field` | parsed offers with per-field provenance and confidence |
| `offer` / `offer_line` | verified commercial offers (subtotal, landed cost, TCO, FX) |
| `negotiation_policy` / `negotiation_message` / `negotiation_round` | versioned policy + audited drafts |
| `approval_chain` / `approval_step` / `approval` | amount/role banded chain and decisions |
| `purchase_order` | award artefacts, split awards, ERP export state |
| `agent_run` / `agent_step` / `tool_call` | full agent reasoning and tool audit trail |
| `audit_log` | append-only, hash-chained security log (immutable by trigger) |
| `usage_event` / `invoice` / `plan` | metering and billing |
| `data_erasure_request` | GDPR/PDPL workflow with statutory deadlines |

`UTCDateTime` (`app/db/base.py`) stores every timestamp as UTC-naive on SQLite and timezone-aware
on PostgreSQL, which keeps `alembic check` clean and comparisons correct in both.

## 6. Frontend architecture

* **Shell** — collapsible navy sidebar (grouped nav, gold active rail, RTL-aware), topbar with
  notifications, locale toggle and a live-update indicator, and a live
  connection badge fed by the WebSocket hub.
* **State** — TanStack Query for server state, tiny local stores for UI preferences; no global
  Redux-style store, no duplicated server state.
* **i18n** — `src/i18n/{ar,en}.ts`, `dir="rtl"` by default, logical CSS properties everywhere,
  Latin digits toggleable, bilingual error strings.
* **Design system** — `src/styles/tokens.css` exposes the Mantiq palette as CSS variables and
  Tailwind maps them via `rgb(var(--x-rgb) / <alpha-value>)` so components never hard-code colour.
* **Accessibility** — semantic landmarks, focus-visible rings, `aria-live` for async results,
  4.5:1 contrast, all motion disabled under `prefers-reduced-motion`.

## 7. Runtime topology

`deploy/docker-compose.yml` runs: `api` (uvicorn), `worker` (Celery), `beat` (scheduler),
`web` (nginx-served static build), `db` (PostgreSQL 16 with the three extensions), `redis`,
`minio`, `otel-collector`. `deploy/helm/procureai` packages the same components for Kubernetes
with HPA, PodDisruptionBudget, secrets via `Secret` refs, and liveness/readiness probes wired to
`/health` and `/ready`.

## 8. Observability

* Structured JSON logs with `request_id`, route, status, latency (`app/core/logging.py`).
* `x-request-id` is echoed on every response and stored on audit rows.
* `/metrics` exposes Prometheus counters/histograms (requests, latency, agent runs, policy blocks).
* An OpenTelemetry collector is part of the compose stack; tracing is opt-in via `OTEL_ENABLED`
  (and Sentry error reporting via `SENTRY_DSN`).

## 9. Failure behaviour

| Failure | Behaviour |
| --- | --- |
| LLM provider down | service raises; API returns `503` with a typed error; no partial commercial state |
| Tool raises | audited as `error`, graph checkpoints, human notified |
| Adapter down (email/WhatsApp) | message row kept `failed`, retried by Celery, buyer notified |
| Policy blocks a send | draft persisted `blocked`, buyer notified, nothing sent |
| Migration failure at startup | logged in lifespan, process still boots to serve `/health` with a clear log |
| Redis unavailable | rate limiting degrades to in-process counter, Celery retries |

Honest gaps are listed in `docs/PROGRESS.md`; they are extension points, not hidden shortcuts.
