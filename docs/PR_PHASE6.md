# Phase 6 — pilot hardening

Seven commits on `arena/a489b365-mantiq-agent` (Arena binds the session to this branch name; it is
delivered from here as instructed). Nothing was deleted to make the pilot look finished: accounting,
demo data, the AI-voice surfaces and the vendor adapters are all still there, behind switches that a
pilot install leaves off.

## What changed

| Task | Commit | Effect |
| --- | --- | --- |
| **A — hide accounting** | `bc64d2a` | `ACCOUNTING_ENABLED` (deployment-level, default `false`). When off the accounting router is never mounted: 0 accounting paths in `/api/openapi.json` (19 when on), every accounting-ish URL answers the same 404 an unknown URL gets, the accounting permissions are stripped from `/auth/me`, no journal/cost/audit-package side effects run, and the post-PO flow still completes. The frontend derives all of it from capabilities — one build, no placeholders. |
| **B — WhatsApp inbound** | `f111790` | Tenancy comes from the payload's `phone_number_id` (the number saved on the Integrations screen), not from a default `nile`. Unknown id → `200 {"processed": 0}` + `channel.unknown_recipient`, so Meta stops retrying and nothing is invented. Signature mismatches are a 401 plus one log line and an audit row (`missing_app_secret` / `bad_signature`), and a strict deployment will not boot a Cloud provider without `WHATSAPP_APP_SECRET`. A message from an active colleague's phone becomes a purchase request through the same intake service `/intake/text` uses (audited `intake.whatsapp`, replied in Arabic/English with the code or the open question); a supplier with an open RFQ still takes the extraction path; an unknown sender still creates nothing. |
| **C3 — voice honesty** | `6a1906b` | Both AI-voice switches default off, and the call screen no longer promises live AI speech: one bilingual line says the call is a scripted transcript, and the action reads "Start a call". |
| **C2 — demo guard** | `73bf533` | `seed-demo` and `bootstrap --demo` refuse in staging and production; a real deployment refuses to *start* while `BOOTSTRAP_DEMO_PASSWORD` is still the shipped `Demo!Pilot2026`. Dev and test unchanged. |
| **C4 — README** | `2fa9522` | The WhatsApp intake claim now describes the behaviour that exists, and the safety section says the Meta/Twilio/SMTP adapters were verified against local servers speaking the documented payloads, never against live vendor APIs. |
| **C1 — Arabic PO PDF** | `15943bc` | Noto Naskh Arabic (Regular + Bold) and its OFL-1.1 licence ship as package data; the faces are registered as *shapable*, so ReportLab shapes through HarfBuzz (`uharfbuzz`, the dependency approved before it was added); Arabic and ASCII are drawn as separate runs in visual order, because one HarfBuzz buffer has one direction and a mixed run mirrors the digits. Descriptions sit in a right-aligned column, numbers in a left-aligned one. |
| **e2e harness** | `9186817` | The journey script seeds its own tenant password instead of the public demo one, because the staging replicas it starts now refuse the default. |
| traceability | `0fd5046` | One row per phase-6 requirement → code → test, with the two honest partials. |

## Evidence (all run in this workspace, on this tree)

| Check | Command | Result |
| --- | --- | --- |
| Lint | `ruff check app tests` | `All checks passed!` |
| Backend tests | `pytest tests -q --junitxml=/tmp/final.xml` | **789 passed, 0 failed, 0 errors, 0 skipped** (baseline before this phase: 753) |
| Migrations | `alembic upgrade head` (SQLite fallback) | ran `0001 → 0017` clean |
| Schema drift | `alembic check` | `No new upgrade operations detected.` |
| Frontend types | `npm run typecheck` | clean |
| Frontend lint | `npm run lint` | clean, 0 warnings |
| Frontend tests | `npm test` | **89 passed in 15 files** |
| Frontend build | `npm run build` | built in 7.7 s |
| End-to-end | `python scripts/e2e_journeys.py` | **15 journeys passed in 11.5 s** — two staging replicas over a real Redis, signed Twilio webhook, fan-out to both sockets, real LLM extraction over HTTP |
| Font packaging | `pip wheel .` | wheel contains `app/assets/fonts/NotoNaskhArabic-{Regular,Bold}.ttf` + `OFL.txt` |

New tests this phase: `tests/test_accounting_hidden.py` (6), `tests/test_api_whatsapp_inbound.py` (25),
`tests/test_services_po_pdf.py` (7), three in `tests/test_cli.py`, one in `tests/test_api_feature_flags.py`,
`frontend/src/accounting.hidden.test.tsx`.

## CI (GitHub Actions, this branch)

All six required checks are green on the final commit: Backend (lint, migrate, tests, coverage),
Frontend (typecheck, lint, tests, build), **PostgreSQL 16 (migrations, RLS, search indexes)**,
Helm chart lint, Secrets & dependency scan, Container images build.

The PostgreSQL job caught a real consequence of hiding accounting, and it was fixed rather than
bypassed: two of its steps exercise the module (`seed-demo` writes the demo chart of accounts, and the
concurrency proof posts a journal entry against accounts 510100/210100). Both now run with
`ACCOUNTING_ENABLED=true`, explicitly; the invariants and the concurrency counts are untouched. The
failure mode was reproduced locally first (switch off → `gl_account` empty → scenario B asserts on it;
switch on → 22 accounts → the scenario runs).

## What could not be run here

1. **The Arabic PDF has never been rendered to pixels.** This sandbox has no `pdftoppm`/`mutool`/`qpdf`,
   and no HarfBuzz rasteriser for Pillow. The tests assert the embedded face, the shaped drawing
   operators and the right-to-left run order; the sample at `/tmp/po_arabic_sample.pdf` is for a human
   to open. `docs/RUNBOOK.md` says exactly what to look for.
2. **No live vendor call.** Meta, Twilio and SMTP were exercised against local servers that speak the
   documented wire formats (and now against two real API replicas over real Redis). A first call to
   each vendor is an integration test with a human watching.
3. **No browser.** Playwright's browser binary cannot be downloaded here, so the browser suite is still
   typechecked only; the same journeys run headless in `scripts/e2e_journeys.py`.
4. **No Docker daemon / no PostgreSQL server.** Migrations ran on the SQLite fallback; the PostgreSQL
   job in CI remains the proof for RLS, `pgvector` and `pg_trgm`.

## Deliberate deviations, and why

* **Voice defaults landed in the accounting commit.** Both are the same question ("what does a pilot
  install offer?") and the same table in `docs/ENVIRONMENT.md`; splitting them would have left the
  environment docs describing a state that never existed.
* **`uharfbuzz` instead of a Python reshaper.** It is the engine ReportLab already binds to; bundling
  the font plus this one wheel gave a self-contained document without hand-rolled Arabic, which the
  task forbade.
* **The webhook never 404s on an unknown number.** Meta retries 404s forever; a 200 with
  `processed: 0` and an audit row is the only answer that stops the loop without inventing a tenant.
