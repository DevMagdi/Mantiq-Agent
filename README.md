# ProcureAI

AI procurement platform for B2B buyers, built by **Mantiq Technologies**. It turns a purchase need
written in Arabic or English into a requisition, an RFQ, parsed supplier offers, a TCO-weighted
comparison, a policy-checked negotiation, and — only after a human approval chain — a purchase
order.

> **The LLM proposes, the policy engine disposes.**
> No autonomous acceptance, signing or payment. Human approval is mandatory before any award.

```
backend/   FastAPI + SQLAlchemy 2 (async) + Alembic + Celery  — 146 API operations
frontend/  React 18 + Vite + TypeScript (strict) + Tailwind  — bilingual AR (RTL) / EN
deploy/    Dockerfiles, compose stack (Postgres 16, Redis, MinIO, OTel), Helm chart
docs/      Architecture, decisions, traceability, security, runbook, API, design system, agent design
```

## What it does

1. **Intake** — a buyer writes "need 30 t rebar 16 mm at 20,500 EGP/t"; text, Excel, e-mail, WhatsApp
   and voice notes are all accepted. The agent asks the clarification questions that make the need
   quotable, then normalises lines against UNSPSC.
2. **Source** — an RFQ is drafted from the requisition and dispatched to a shortlist over e-mail,
   WhatsApp, an AI voice call (disclosure first, consent-gated recording) or a no-login web form.
3. **Extract** — supplier replies are parsed into offers with per-field provenance and confidence;
   anything uncertain lands in the human verification drawer.
4. **Compare** — landed cost (freight, duty, FX) and TCO are computed, then weighted into a ranking.
   The recommendation is written from those numbers only; fabricated figures are rejected.
5. **Negotiate** — a versioned policy (target, walk-away, rounds, concessions, tone) gates every
   counter-offer. Competitor names and exact prices can never leave the building.
6. **Approve & award** — an amount/role-banded approval chain; the final human decision issues the
   purchase order (with split-award support) and exports it to the ERP.
7. **Measure** — savings, cycle time, supplier response rates, spend by category/supplier, and an
   explainable forecast.

## Quick start (local, no external accounts)

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp ../deploy/.env.example .env          # set SECRET_KEY and ENCRYPTION_MASTER_KEY
procureai migrate && procureai bootstrap --demo && procureai seed-demo
uvicorn app.main:app --reload --port 8000

# frontend
cd ../frontend
npm ci && npm run dev                   # http://localhost:5173 (proxies /api to :8000)
```

Demo tenant **Nile Industries**, password `Demo!Pilot2026`:
`manager@nile.example.com`, `buyer@nile.example.com`, `requester@nile.example.com`,
`finance@nile.example.com`; platform superadmin `admin@pilot.procureai`.

Docker stack instead: `cd deploy && cp .env.example .env && docker compose up -d --build`
(api on :8000, web on :8080).

## Verify it

```bash
cd backend && pytest tests -q --cov=app        # 513 tests, 85% line coverage
cd backend && ruff check app tests
cd backend && alembic upgrade head && alembic check
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
```

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | layers, data model, tenancy, agent runtime, topology |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 30 architecture decisions with rationale |
| [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) | FR/NFR/AC → code → test, with honest status |
| [`docs/SECURITY.md`](docs/SECURITY.md) | threat model, controls, PII, AI safety, operator checklist |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | install, upgrade, jobs, monitoring, incident playbooks |
| [`docs/API.md`](docs/API.md) | conventions, lifecycle walkthrough, full endpoint inventory |
| [`docs/DESIGN_SYSTEM.md`](docs/DESIGN_SYSTEM.md) | tokens, typography, RTL, accessibility, components |
| [`docs/AGENT_DESIGN.md`](docs/AGENT_DESIGN.md) | graph, tool registry, policy engine, guardrails, evals |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | what is done, partial, or deliberately absent |

## Safety commitments

* The agent has **no tool** to accept, sign, pay or change specifications — an import-time assert
  keeps it that way.
* Supplier content is fenced and treated as data; a 40-payload injection corpus runs in CI.
* Negotiation output is checked against walk-away, round caps, concessions, opt-in and competitor
  confidentiality before it can be sent.
* AI voice calls state that they are AI first and record only with consent.
* Supplier PII is envelope-encrypted per tenant; logs never contain secrets or full contacts.

## License

Proprietary — © Mantiq Technologies. All rights reserved.
