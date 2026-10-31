# Architecture Decision Records

Every consequential ambiguity in the brief was resolved once, in the most defensible
enterprise-grade direction, and recorded here. Format: **decision — why — consequence.**

## Stack & infrastructure

1. **SQLite in dev/test, PostgreSQL 16 in production.** The sandbox has no Docker daemon or
   Postgres; a single async SQLAlchemy codebase with a `dialect.name == "postgresql"` guard for
   RLS/pgvector/JSONB keeps production hardening real without pretending it ran locally.
2. **Alembic is the only schema authority.** Twelve linear revisions, `alembic check` must stay
   clean; ORM models and migrations are edited together (a mismatch fails CI).
3. **Celery tasks are thin wrappers over service functions.** Schedulers must never hold business
   logic; every rule stays testable without a broker. When Redis is absent the API still works and
   jobs can be triggered by hand (`procureai purge`, `procureai fx-refresh`) or by awaiting the task
   functions directly, which is how the test suite runs them.
4. **Adapters always ship with a fake — but a fake never reaches a real deployment.** In `dev`/`test`
   `registry.resolve(...)` still returns the deterministic fake so the suite is hermetic and a demo
   runs with zero external accounts. In `staging`/`production` the fakes are refused (see §97): the
   original wording of this entry ("otherwise the deterministic fake") was the shape of the bug this
   project had to remove.
5. **No MinIO in dev.** `LocalStorage` implements the same `StorageAdapter` protocol under
   `backend/var/storage`; compose wires MinIO for production.

## Tenancy & identity

6. **`tenant_id` on every business row, explicit in queries, RLS in Postgres.** Middleware never
   rewrites queries; services take the tenant object. Defence in depth without magic.
7. **Foreign ids in another tenant return `404`, never `403`.** A `403` is an existence oracle.
8. **Argon2id password hashing, forced rotation on bootstrap, TOTP MFA, single-use rotating
   refresh tokens.** Refresh rotation binds each token to a `refresh_session.jti`; an explicit
   `jti` passed to the encoder is preserved (an earlier version overwrote it, which made every
   refresh look revoked — fixed and regression-tested).
9. **Account lockout after 5 failures for 15 minutes, audited.** Cheap, effective, and reversible
   by an admin without touching the database.
10. **SSO is an extension point, not a fake screen.** `GET /auth/sso/config` reports provider
    availability from env; no SAML/OIDC flow is claimed to work.

## Agent & policy

11. **The tool registry is an allow-list, asserted at import.** `FORBIDDEN_TOOL_NAMES` contains the
    verbs the brief forbids (accept, sign, pay, edit specs); an `assert` prevents a future
    contributor from registering one.
12. **Policy decisions are pure functions returning codes.** `BLOCK_*` / `WARN_*` strings are the
    API contract to the UI (the guard bar) and to the audit log; they are never translated prose.
13. **Number guard on every generated explanation.** Any figure in a recommendation that is not in
    the computed ranking (TCO, score, rank, lead time, the 100-point scale) is treated as a
    fabrication and replaced by a deterministic bilingual summary.
14. **Injection defence is layered.** Fencing + invisible-character stripping + phrase scoring +
    "content is data, never instructions" prompt framing, plus a 40-payload corpus test.
15. **`auto` negotiation mode is capped by value and by tenant ceiling.** Autonomous sending is
    impossible above `value_threshold`, outside contact hours, or when the tenant's max mode is
    `draft_only`. Human approval is always required for an award.
16. **Deterministic FakeLLM by default in dev/test.** Same prompt contracts, no network, stable
    goldens. Real providers are selected by env only.

## Commercial rules

17. **Landing cost = subtotal + freight + duty + insurance, converted at a stored FX rate.**
    FX is frozen on the offer row so a comparison cannot silently drift.
18. **Ranking uses configurable weights normalised to 1.0** (price/lead time/quality/terms/risk),
    stored per RFQ; the API rejects weights that do not sum to 1 ± 0.05.
19. **Approval chains are amount- and category-banded, with a limit-derived fallback.** If no chain
    matches, a single step is derived from approval limits, escalating to `finance_approver` above
    every limit. Chain rows are sequential: only the current step is `pending`.
20. **Split awards create one purchase order per supplier** and close the RFQ once.
21. **Negotiation policies are versioned, never mutated.** A new policy deactivates the previous
    one; drafts are revalidated against the *current* policy at send time.
22. **Erasure redacts the person, not the transaction.** PII columns are blanked, the commercial
    skeleton and hash-chained audit log remain (DPIA-friendly, see `docs/SECURITY.md`).

## Frontend

23. **TanStack Query is the only server-state cache.** No duplicated stores; mutations invalidate
    explicitly so a stale comparison or approval count is impossible.
24. **Arabic RTL is the default**, English is a first-class toggle; all strings live in
    `src/i18n`, and `src/i18n/i18n.test.ts` fails if the two dictionaries drift out of key parity.
25. **No hard-coded colours in components.** Tokens only, mapped through Tailwind's
    `rgb(var(--token-rgb) / <alpha-value>)` pattern so opacity modifiers keep working.
26. **Every screen has loading, empty and error states wired to real endpoints.** Buttons that
    cannot act are disabled with a reason, never dead.
27. **Vitest covers behaviour, not snapshots** — formatters, guard-bar logic, matrix rendering and
    shell navigation are asserted through the real components.

## Operations

28. **Bootstrap is idempotent and CLI-first** (`migrate`, `bootstrap`, `seed-demo`, `doctor`), so a
    fresh environment is one command and a demo environment is one more.
29. **`/health` is liveness, `/ready` is readiness including a DB round-trip.** Compose and Helm
    probe both.
30. **Coverage is measured with `concurrency = greenlet,thread`.** SQLAlchemy's async engine runs the
    sync ORM inside greenlets; without this the tracer loses every line after an endpoint's first
    `await` and under-reports API coverage by ~35 points. The setting lives in `pyproject.toml`.

## Post-PO lifecycle (delivery → match → settlement)

31. **One status axis per object, one owner per transition.** The purchase order moves
    `issued → in_transit → partially_received → received → confirmed → closed`; `received` is the
    warehouse's fact, `confirmed` is the requester's judgement. `close_po` refuses anything that is
    not `confirmed`, so a delivery can never be closed by the team that booked it.
32. **Separation of duties is enforced by permissions, not by convention.** `receipt:create`
    (warehouse) and `receipt:confirm` (requester/procurement/finance) are separate grants; the demo
    `warehouse` role has the first and not the second. The receipt drawer is hidden and the endpoint
    returns 403 with `required_permission` if a storekeeper tries to sign off.
33. **The three-way match never trusts the invoice.** Quantities are capped by the *confirmed*
    receipt (not by what the warehouse typed), prices are compared with the PO line inside a 0.5%
    tolerance, and previously invoiced quantities are subtracted so partial invoicing cannot
    double-bill. Over-invoicing is a hard `QTY_EXCEEDS_RECEIPT`; the tolerance is per line, not per
    total, because a cheap line cannot mask an expensive one.
34. **A mismatch is approvable only with a written reason.** `approve_invoice` refuses a non-matching
    invoice unless `override_reason` is supplied; the text is appended to the invoice notes and the
    approval audit row records `overridden: true`. Silence is never consent.
35. **Payments are recorded, never executed.** `record_payment` writes a ledger credit with a
    mandatory external reference against an already-approved invoice and rejects overpayment. There
    is no bank/payment adapter in the codebase, and the payment suggestion carries
    `execution: "outside_platform"` so no client can mistake it for a transfer.
36. **The supplier balance is derived, not stored.** It is the sum of the ledger (invoices debit,
    payments credit) computed per request, so it can never drift from the documents that created it.
    `overdue` is a filtered view of the same rows rather than a second counter.
37. **The post-award loop is outside the agent graph.** `STAGES` still ends at `approval`; receiving,
    matching, approving and paying are RBAC-guarded service calls made by named humans. A test fails
    the build if a payment/receipt tool is ever registered.

## Frontend theme

38. **There is exactly one theme: navy.** The light theme, `[data-theme]` blocks, the `darkMode`
    Tailwind switch, `User.theme`, the `PATCH /auth/me` preference and the toggle in the shell were
    deleted (migration `0014_drop_theme_preference` drops the columns). Two palettes meant every
    screen, PDF and screenshot had to be reviewed twice; the institutional navy is the brand.
39. **Semantic colours are split into fill and ink.** `--ok/--warn/--danger/--info` are 2.4–4.3:1 as
    text on the card surface, so they are fills/borders/chart marks only; text uses
    `--ok-ink/--warn-ink/--danger-ink/--info-ink`, the same hues lightened past WCAG AA (5.7–8.2:1).
40. **The rail collapses and remembers.** `localStorage["procureai.sidebar"]` holds the state (a UI
    preference, not a tenant setting); collapsed keeps 40px targets and `aria-label`s, a hairline per
    group and the gold active rail drawn with a logical `start-0` pseudo-element so RTL mirrors for
    free.

## Test determinism

41. **The suite never reads the wall clock for contact hours.** `CONTACT_HOURS_START/END` are tenant
    settings (default 07:00–21:00, the real business rule) and `tests/conftest.py` widens them to
    00:00–23:59 for the session. Before this, `tests/test_api_flow.py` passed in the morning and
    failed after 21:00 Cairo because dispatch was legitimately blocked. The hours rule itself is
    still asserted with an explicit `now` in `tests/test_policy_engine.py` and
    `tests/test_services_negotiation.py`.

## Database policy

42. **PostgreSQL 16 is the only supported database; SQLite is an isolated test/sandbox fallback.**
    `database_url` now defaults to `postgresql+asyncpg://procureai:change-me@localhost:5432/procureai`.
    SQLite starts only when `APP_ENV=test` or `ALLOW_SQLITE_FALLBACK=true`; otherwise startup raises
    with the remedy inline. The reason is not taste: `tenant_id` RLS, `pg_trgm` ranking, pgvector
    similarity and `citext` simply do not exist on SQLite, so a "supported" SQLite deployment would
    silently lose tenant isolation. The API is still SQLAlchemy 2 async on both engines.
43. **The PostgreSQL settings are production settings, not defaults of convenience.** Pool
    `10 + 20 overflow`, recycle every 1800 s, and `statement_timeout=15000` applied per connection via
    `server_settings` (SQLite fallback gets `busy_timeout` + `synchronous=NORMAL` instead). The
    timeout is deliberate: a runaway analytics query must fail inside the request budget rather than
    pinning a pool slot.
44. **`metadata.create_all` can no longer paper over a failed migration outside dev.** The Alembic
    fallback runs only for `APP_ENV != prod` **and** only on the SQLite fallback; on PostgreSQL a
    failed `alembic upgrade` aborts startup, because `create_all` cannot create the RLS policies,
    extensions or triggers and would leave a database that looks healthy but is not protected.
45. **RLS policies compare `tenant_id` as text.** `0010` originally cast the GUC to `uuid`
    (`current_setting('app.tenant_id')::uuid`), which fails on every table because `tenant_id` is
    `varchar(36)`: the whole migration chain died at the first policy on real PostgreSQL. Policies now
    use `NULLIF(current_setting('app.tenant_id', true), '')`, verified by `scripts/verify_rls.py`
    (own rows visible, other tenant's rows 0, cross-tenant move blocked by `WITH CHECK`, append-only
    audit log).
46. **Revision ids are capped at 32 characters.** Alembic hard-codes `alembic_version.version_num`
    as `varchar(32)` and PostgreSQL enforces it — `0012_bootstrap_seed_reference_data` (33) aborted the
    chain on PostgreSQL while passing on SQLite. The revision was renamed and `migrations/env.py` now
    asserts the limit at import, so the failure moves from deploy time to lint time.
47. **Raw-SQL search objects are excluded from autogenerate.** `embedding_vec vector(1536)` and the
    `pg_trgm`/HNSW indexes exist only in migration `0011` (SQLAlchemy cannot express them, and SQLite
    `create_all` would choke), so `alembic check` would propose dropping them forever. `include_object`
    filters exactly those six indexes and one column by name.

## Verification

48. **GitHub Actions now runs a real PostgreSQL 16 job.** The `postgres` job migrates from scratch on
    `pgvector/pgvector:pg16`, runs `alembic check`, asserts SQLite is refused outside test/sandbox and
    runs `scripts/verify_rls.py`. Previously every CI run was SQLite-only, which is exactly how the
    two PostgreSQL-only defects above survived.
49. **The load test is a script in the repo, not a claim in a document.**
    `scripts/load_test.py --users 50` drives 50 independent sessions with per-role endpoint mixes,
    think time and live WebSockets, and fails on p95 > 800 ms, p99 > 2000 ms, any 5xx or any 403.
    Results and caveats are in `docs/LOAD_TEST.md`.
50. **Encrypted PII fails closed, never 500s.** `get_dek` now raises `CryptoError` when the master
    key cannot unwrap a tenant DEK (secret rotated or a different key was used to seed), and
    `decrypt_text` catches it: the read returns nothing, one error is logged per tenant, and the
    ciphertext stays untouched. Before this, one unreadable supplier contact turned
    `GET /suppliers` into a 500 for the whole tenant — found by the 50-user load test.
    `encrypt_text` still propagates the error, so nothing can silently re-key existing data.
51. **"All day" contact hours are `0..24`, not `0..23`.** The policy engine compares
    `time(start) <= now <= time(end)`, so a test window of `0..23` still closed at 23:00 local and
    the voice/negotiation tests failed only when CI ran after 23:00 Cairo. `end = 24` now means
    end-of-day (unrestricted) and is used by the test harness and by web forms, which are not a
    contact window at all.
52. **Commercial balances are withheld without `ledger:read`.** `GET /purchase-orders/{id}` returns
    `supplier_balance: null` for roles such as `warehouse`; the PO detail page renders without the
    balance card and the list never calls `/supplier-balances`. A warehouse user needs the order to
    record a receipt, not the supplier's financials.
53. **One theme, enforced by the token file.** `tokens.css` declares only navy surfaces and a gold
    accent and sets `color-scheme: dark`. There is no `data-theme`, no `.dark` variant and no
    `prefers-color-scheme` media query anywhere in the built CSS, so a light theme cannot come back
    through a component-level override: any colour that is not a token fails review.
54. **Gold is an accent, never a surface.** Gold appears only as a 2 px active spine, a wash, a
    hairline or a focused border. Primary buttons are the one exception (`--grad-gold` with
    `--gold-contrast` text, 7.24:1), because a first-priority action must be the brightest thing on
    a dense page.
55. **Every rail label clears WCAG AA on both rail gradients.** `--sidebar-label` was `#66798f`
    (4.21:1 on `--sidebar`, 4.0:1 on the active wash — technically AA-large-only at 10 px, i.e. a
    fail). It is now `#70849b` = 4.90:1 on the rail and 4.66:1 at the top of the rail gradient. `--ink-subtle`
    was likewise `#7b8b9e` (4.19:1 on a raised card) and is now `#8595a8` (4.77:1). Every ink/surface
    pair is measured in `docs/DESIGN_SYSTEM.md` and asserted by `src/styles/tokens.test.ts`.
56. **State is expressed once, in HTML.** Toggles are `<button aria-pressed>` styled by
    `.chip[aria-pressed="true"]` / `.segmented-item[aria-pressed="true"]`, not by conditional class
    strings. The selector is both the style and the test hook, and screen readers get the state for
    free instead of from a class name.
57. **`table-scroll` caps table height, not the page.** Long lists scroll inside the card with a
    sticky header, so the rail and page heading stay put — the dense-table pattern used by
    procurement back-offices rather than full-page scroll.
58. **The HTML/API contract for clarifications is unchanged; only the UI enforces it.** The backend
    keeps returning 422 when a request is submitted with open questions (`AC-*` unchanged), and the
    detail page disables Submit, explains why with `aria-describedby`, and offers waive per question.
    Adding a second, softer server rule would have hidden the guard the SRS asks for.

## Phase 2 — purchase-to-pay and accounting

59. **The eleven P2P stages are derived from documents, not stored as a status field.** A stage is
    reached only when its evidence exists (RFQ sent, offers present, all approval steps decided, PO
    issued, receipt, confirmation, sealed package, posted invoice). `p2p_stage_event` records *when*
    each stage was first reached, by whom and from which action, so the history cannot be rewritten
    by later edits to the documents — but the current stage can never disagree with the papers.
60. **The stage log and the ledger lines are append-only.** Migration `0015` grants the application
    role `SELECT/INSERT/UPDATE/DELETE` on the new tables and then revokes `UPDATE`/`DELETE` on
    `journal_line`, `cost_allocation` and `p2p_stage_event`; `scripts/verify_rls.py` proves both the
    revocation and per-tenant isolation on a real PostgreSQL 16 in CI. A stage that was reached cannot
    be un-reached and a posted number cannot be rewritten: corrections are new rows.
61. **One chart of accounts per tenant, seeded with stable codes.** `110100` inventory, `110200`
    GR/IR, `120100` VAT input, `210100` AP, `100100` bank, `510100` materials, `530100` price
    variance, `540100` rounding, `220100` accrued, `300100` retained earnings. Tenants may rename
    them; the codes are the contract that makes automatic postings reproducible.
62. **GR/IR is a real clearing account, and the posting path depends on whether the receipt was
    accrued.** With a posted receipt accrual, the invoice clears `110200` at *PO* cost and books the
    price difference to `530100` (debit when unfavourable, credit when favourable). Without one, the
    invoice debits inventory directly per line. Both paths are tested to the fils, because a system
    that books the same invoice two ways without saying which one ran is unauditable.
63. **An invoice posting requires three human facts, in this order: a clean match, an approved
    invoice, and an accepted audit package.** The API returns `match_not_clean`,
    `invoice_not_approved` or `audit_review_required` respectively. The audit gate can be disabled per
    tenant (`settings.accounting_require_audit_review = false`) for a small entity that cannot staff a
    separate auditor — disabling it is a tenant decision, not a code path an agent can reach.
64. **Two-person control on manual entries only.** A document-derived entry has already passed the
    auditor and the invoice approver, so the controller who drafted it may post it. A *manual* entry
    (`source_type = "manual"`) cannot be posted by its creator: that is the one case where a single
    person could otherwise invent a journal out of nothing.
65. **Posted entries are immutable; corrections are reversals.** `reverse_entry` refuses an already
    reversed entry, requires a written reason, mirrors every line with debits and credits swapped,
    links the two rows (`reversal_of` / `reversed_by`) and excludes the original from the trial
    balance *only through its mirror*. The trial balance sums `posted` **and** `reversed` rows so a
    reversed entry and its mirror net to zero instead of vanishing.
66. **Analytical accounting rides on the posting, not on the document.** Every inventory/expense debit
    needs a cost centre (`missing_cost_center`), and the posting writes a `cost_allocation` fact with
    the cost centre, unit number, item and quantity. Cost reports read the facts, so a later edit to
    a PO cannot silently move cost between plants. System accounts (GR/IR, VAT, variance, rounding,
    AP, bank) are exempt from the cost-centre rule.
67. **The auditor package is sealed, not regenerated on read.** Each component is SHA-256 hashed, the
    hash list is hashed into a manifest, and `GET .../audit-package` re-verifies the manifest on every
    read (`verification.intact`). Re-assembling after new evidence resets a previously accepted
    package to `assembled` and clears the reviewer: a seal that describes stale documents must not be
    able to unlock a posting.
68. **Assembly is automatic on confirmation, and audited.** The package is produced inside
    `confirm_receipt` the moment the requester confirms the delivery (the point at which the evidence
    is complete), with an `audit_package.assembled` audit row marked `automatic: true`. A failure to
    assemble is logged but never rolls back a physical confirmation.
69. **`controller` and `auditor` are separate roles, and neither can approve a purchase.**
    `controller` owns draft/post/reverse plus cost master data; `auditor` is read-only on money with
    `audit_package:review` — the reviewer cannot post, and the poster cannot self-review. Both are
    seeded by migration `0015` and re-synced from `app/core/rbac.py` at bootstrap.
70. **A partial receipt only accrues the increment.** `draft_receipt_accrual` subtracts everything
    already accrued for the order (drafts included) per line and posts the difference; a second call
    with nothing new returns `duplicate_accrual` rather than a zero-value journal.

## Channels, WhatsApp and the unified inbox (phase 3)

71. **One resolver, no ad-hoc channel checks.** `services/channels.resolve()` is the only place that
    answers "where does this message go". It evaluates, in order: RFQ override → supplier preferred →
    supplier secondary → `whatsapp, voice, email, web_form`, and a candidate only wins when it is
    reachable (address on file, consent for WhatsApp/voice, supplier not blacklisted, tenant and
    deployment switches on). Consequence: the RFQ screen, the inbox, the reminder job and the
    missing-information follow-up cannot disagree about the channel.
72. **An override is honoured even when the channel is not reachable — if a reason is given.** A buyer
    who deliberately chooses voice for a supplier who has not consented must supply a reason, which is
    written to `channel_event` and the audit log; without a reason the API returns
    `channel_unavailable` with the per-channel reasons. Silent fallback would hide a compliance
    problem, and a hard refusal would block the field team's real workflow.
73. **Consent is per channel, per supplier, and the supplier's own words win.** `STOP`/`START` (AR and
    EN) on any channel are processed before message ingestion and never reach the extraction pipeline;
    an inbound WhatsApp reply by itself records an opt-in for WhatsApp (the number demonstrably works),
    with the evidence stored. Email is implicitly permitted for legitimate B2B RFQ contact until an
    explicit opt-out, which is the only channel where that holds.
74. **WhatsApp is a first-class transport, not a text shooter.** The provider contract covers text,
    approved templates (`whatsapp_template_name`), documents and interactive messages; a structured
    quote request is ONE interactive message (Flow when configured, reply buttons otherwise) asking for
    the three numbers a supplier can answer from a phone: unit price, lead time, validity. Sending the
    body first and then "reconciling" rows was rejected in favour of a single audited message row.
75. **Receipts only move forward.** `apply_delivery_status` enforces a rank
    (`sent < delivered < read`), returns `stale_status` for a late `delivered` after a `read`, and
    `unknown_message` for an id it does not own. WhatsApp can replay receipts; a status column that can
    go backwards is not evidence.
76. **Threads are per (RFQ, supplier); channel lives on the message.** `MessageThread.channel` is the
    current channel for convenience, but every `ChannelMessage` keeps its own `channel`, and switching
    never rewrites history. The inbox therefore derives its channel set from messages, which is the
    only way "switched mid-conversation with history preserved" can be true.
77. **`inbox.reply` always resolves the canonical thread.** It re-runs `ensure_thread` for the pair, so a
    reply lands where the outbound history lives even if the caller holds a stale row, and returns
    `switched_from` so the UI can say "moved from email to WhatsApp".
78. **A consent refusal is a stored verdict, not an HTTP error.** WhatsApp send endpoints return `200`
    with `ok: false`, `codes: ["BLOCK_NO_OPT_IN"|"BLOCK_FEATURE_OFF"|"BLOCK_CHANNEL_UNAVAILABLE"]` and the
    policy snapshot, because the refusal is part of the communication record. Transport failures are
    separate (`status: failed`). Clients read `ok`, not the status code.
79. **Communication evidence is append-only.** `channel_event`, `voice_call_turn` and `assistant_turn`
    have RLS like every tenant table and `UPDATE`/`DELETE` revoked from the application role
    (migration `0016`, re-checked by `scripts/verify_rls.py`).
80. **Reminders follow the resolved channel.** `send_reminders` no longer hardcodes email: it resolves
    per supplier, renders the template in the supplier's language, records `channel` and `comm_status`
    on the invitation, and keeps the 2-day/2-reminder cadence. The same resolution now drives the
    missing-information follow-up in `extraction.request_missing_info`.
81. **20 bilingual templates per tenant, kernel-seeded not copy-pasted.** Six phase-1 templates plus
    fourteen phase-3 rows (WhatsApp invitation/reminder/counter-offer/quote request/missing info in AR
    and EN, email equivalents, voice scripts) live in `catalog_data.DEFAULT_TEMPLATES` for new tenants
    and in migration `0016` for existing ones; both paths are idempotent on
    (code, channel, locale), and every row carries `kind` and its extracted `variables`.

## AI voice and the internal assistant (phase 3)

82. **Two switches, not one.** A capability needs both the deployment setting (`WHATSAPP_ENABLED`,
    `VOICE_AI_ENABLED`, `VOICE_ASSISTANT_ENABLED`) and the tenant flag in `tenant.settings["features"]`;
    `services/features.is_enabled()` is the only reader. Unknown keys are rejected, so a typo cannot
    silently disable a control, and every entry point fails closed with `BLOCK_FEATURE_OFF`.
83. **The AI voice agent is deterministic and policy-first — no free-form LLM in the call path.** Turns
    are classified by a keyword model (`classify_question`) into approved topics (price, validity,
    payment terms, delivery, specification, consent) and blocked ones (competitor pricing, commitments
    and award intent, specification changes, payment execution). Answers are assembled from approved
    RFQ data only (`approved_knowledge`, `knowledge_version: approved-data-v1`); a blocked question
    gets a fixed refusal in the supplier's language and a `block_code`. This is deliberate: a guardrail
    that is a prompt can be talked out of it, a guardrail that is code cannot.
84. **Disclosure first, consent before recording, per turn audit.** `voice.start_call` plays the
    disclosure, records the consent answer, refuses to record without it, and enforces country calling
    hours. Every audible turn — the agent's own replies included — is persisted with `seq`, `topic`,
    `decision`, `block_code` and `reason`, and the call's `policy_snapshot.ai_agent` summarises
    `topics_answered`, `topics_blocked` and `blocked_turns`.
85. **A call feeds the existing verification queue, never an offer.** Extracted fields from the
    transcript go through `extraction` exactly like an email reply and stay human-verified; the agent
    cannot create an approved offer, cannot accept, cannot renegotiate and cannot change a line.
86. **The internal assistant is read-only by construction.** Intents are resolved before any query and
    each declares the permission it needs (`assistant.INTENT_PERMISSIONS`); approve/award/sign/pay/post
    phrases classify into `forbidden` and return `ASSISTANT_FORBIDDEN_ACTION` with guidance to the
    human screen. The only write is `pr_create`, which calls the normal intake service and leaves a
    `draft`/`clarifying` requisition the user submits themselves. Every response carries `can_act:
    False`, and every turn is stored (`assistant_session`/`assistant_turn`) and audited.
87. **Ownership is checked after permission.** A requester may hold `po:read` and still get
    `ASSISTANT_NOT_FOUND` for an order that is not theirs; a missing permission returns
    `ASSISTANT_PERMISSION` before any lookup. The response never includes the number it refused to
    give.
88. **Speech is untrusted input.** `POST /assistant/transcribe?ask=true` runs the same intake scanning
    as any other text before answering, and the transcript is stored on the turn with its confidence;
    the assistant is never granted a capability because the request arrived by voice.
89. **Over-receipt is checked cumulatively, never per delivery.** A receipt line is measured against
    what is still owed — ordered quantity minus everything already booked as `received` or
    `confirmed` — not against the ordered quantity on its own. Two deliveries of 60 against an order
    of 100 used to both pass; the second is now refused with `OVER_RECEIPT`, and the message names
    the quantity that was already received so the warehouse can see why. The check is the same for
    full and partial receipts, and the refusal is a 422 whose `issues[]` carries the per-line detail.
90. **Every read-modify-write aggregate is locked before it is read.** `common.lock_for_update`
    issues `SELECT … FOR UPDATE` (plus `populate_existing`, tenant-scoped whenever the model carries a
    `tenant_id`) and is taken before the check it protects: the purchase order before the receipt
    quantity test, the supplier invoice before a payment, the journal entry before posting, the
    approval before a decision. Without those locks the invariants are only true when nobody is
    concurrent: `scripts/concurrency_check.py` shows, with the four locks disabled, 8 simultaneous
    half-receipts booking **400 units against an order of 100**, one draft journal entry posted
    **six times** and one invoice paid **five times**. With the locks in place the same bursts produce
    exactly two receipts, one posting and one payment. The cost is honest too: the hot row is
    serialised, so eleven simultaneous receipts against *one* order reach p95 ≈ 1.3 s while the
    aggregate p95 across the run stays at 188 ms (docs/LOAD_TEST.md).
91. **A second decision on an already-decided approval is an error, not a silent no-op.** The
    approval service returns `{ok: false, error: "Approval is 'approved', not pending."}`; the
    endpoint used to answer 200 with that body, which looks like success to a double-clicking user
    and to any client that only checks the status code. It now raises `ValidationFailed` (422) like
    every other refused transition in the API.
92. **Rate limiting is per identity, not per IP.** Authenticated traffic is bucketed as `user:<sub>`
    (240 requests/min), anonymous traffic as `ip:<host>` (600/min), and login is limited both per
    account (10/min) and per address (120/min) so one office behind a single NAT address cannot
    lock the others out, and fifty load-test users on one IP are not throttled as one client.
93. **The load test measures the write path, not the refusal path.** The demo dataset has a single
    purchase order and it is already fully received, so every goods receipt used to be refused by
    the (correct) over-receipt guard and the write path was never exercised. The harness now seeds
    five large, freshly issued orders (`--receipt-orders`, `--no-seed` to opt out) that the warehouse
    personas receive against, and it resolves `{po_id}`-style ids **per virtual user** instead of
    always taking the first row: fifty users posting to one order measure a row lock, not a system.
94. **The concurrency proof is part of CI.** `scripts/concurrency_check.py` runs in the `postgres` CI
    job right after the RLS verification, against a real PostgreSQL 16 with the seeded demo data, so
    a future change that drops a lock or reorders a check fails the build instead of quietly
    reopening the race.
95. **Hot read paths batch their lookups.** `analytics.spend_breakdown` resolved the supplier, RFQ,
    requisition, plant and category once *per purchase order* (55 ms of query time for twelve
    orders, linear afterwards) and the supplier list resolved the primary contact and the
    certifications once *per row* (≈50 queries for a 25-row page). Both are now a fixed number of
    `IN` queries: spend 55 ms → 5 ms, supplier list 45 ms → 13 ms over HTTP, same payloads.
96. **The dashboard reads the price history once.** The chart and the forecast each ran the same
    query; they now share one pass (the dashboard's own latency fell from ~60 ms to ~33 ms warm,
    on top of the batching above).

## Phase 5 — real providers, live fan-out, browser tests

97. **A fake provider is a startup error in `staging`/`production`.** One table
    (`app/core/providers.py`) names the variables each provider needs, a second list names the
    providers that are *critical* (email and LLM always; WhatsApp when the channel is enabled;
    telephony/STT when AI calls are enabled). `assert_ready()` runs inside the lifespan and refuses to
    boot with every missing variable in one message; `registry.build` and `default_provider()` raise
    `ProviderNotConfigured` if a request path asks for a fake anyway. Optional capabilities (SMS,
    search, OCR, ERP, payments) only log a warning — refusing to boot because nobody configured
    supplier discovery would be theatre. `ALLOW_FAKE_PROVIDERS=true` is the single, explicit,
    loudly-logged escape hatch, and it exists for staging demos, not deployments.
98. **Twilio is the second WhatsApp transport and the only SMS/voice transport.** Most Egyptian and
    Gulf enterprises already hold a Twilio (or BSP) contract that covers WhatsApp, SMS and voice on
    one credential pair; Meta's Cloud API stays supported for tenants who prefer it. One adapter
    family, one retry policy, one signature scheme, three channels. `TELEPHONY_*` remains as an alias
    so an existing deployment does not break, but `TWILIO_*` wins.
99. **Only retryable failures are retried, and only twice.** 429/5xx/transport errors back off
    0.5 s → 1 s; a 4xx (bad number, unapproved template, insufficient funds, rejected call) returns
    immediately with the vendor's numeric code. Retrying a 400 on a messaging API costs money and
    duplicates supplier messages; retrying a 429 does not. The same helper serves messaging and voice
    so the policy cannot drift between channels.
100. **Webhook signatures are verified or the request is dropped.** Twilio's HMAC-SHA1 over the public
    URL plus the sorted form parameters; Meta's HMAC-256 for the Cloud API. `TWILIO_WEBHOOK_URL`
    exists because behind a proxy `request.url` is the internal address and every signature would
    fail — an operator sets the public URL once. Fixing this uncovered a real hole: the SMS callback
    decided verification from `WHATSAPP_PROVIDER`, so a deployment with Meta for WhatsApp and Twilio
    for SMS skipped the check entirely. Verification now follows the provider selected *for that
    channel*.
101. **Redis pub/sub, not Streams, for live events.** The feed carries "something changed, refetch"
    hints; the client already refetches state, so at-most-once delivery is the right contract and a
    dropped frame costs a stale badge until the next poll, not data. Streams would add consumer-group
    bookkeeping and replay we have no consumer for. A publish that cannot reach Redis still reaches
    the local sockets and is logged at error level, because a degraded fan-out is an incident, not a
    detail.
102. **Publish locally first, and never echo your own envelope.** Each process has an origin id; the
    local broadcast happens before the Redis round trip (a socket on this replica must not wait on the
    network) and the listener skips envelopes with its own origin. This is what keeps a single-replica
    dev box fast and makes duplicate frames impossible under fan-out.
103. **Structured answers come from a forced tool call, never from parsing prose.** Anthropic gets
    `tool_choice: {type: tool, name: emit_structured_output}` and the schema as that tool's
    `input_schema`; OpenAI-compatible endpoints get `response_format: {type: json_object}` plus the
    schema in a system message. Free-text extraction is what silently invents quantities.
104. **`LLM_BASE_URL` means two different things, deliberately.** For Anthropic it is a *gateway* in
    front of `/v1/messages` (default `https://api.anthropic.com`); for an OpenAI-compatible endpoint it
    is the versioned base (`https://api.openai.com/v1`, `http://vllm:8000/v1`). The path is appended,
    never guessed, and `docs/ENVIRONMENT.md` says so next to the variable.
105. **SMS is a first-class channel, not an afterthought.** Several suppliers in the target market have
    no WhatsApp but do have a phone; `send_message` grows an `sms` branch with the same consent,
    policy and dispatch bookkeeping as the others, and inbound SMS goes through the same extraction
    pipeline. `docs/ENVIRONMENT.md` lists what it needs.
106. **The test suite proves the real clients over a real socket.** Every provider test starts a local
    HTTP server that speaks the vendor's wire format (`tests/test_adapters_twilio.py`,
    `tests/test_llm_real_path.py`, `tests/test_ws_fanout.py` starts a real `redis-server`). Nothing
    inside the application is patched; only the vendor's own service is replaced by one we control,
    because a vendor account cannot exist in CI.
107. **Ship the browser suite; do not pretend to run it.** `frontend/e2e` covers sign-in/out, the
    RTL↔English switch, every route and the WebSocket handshake, and `npm run e2e:typecheck` runs in
    CI. The browsers themselves cannot be downloaded in this sandbox (the Playwright CDN is blocked),
    so no run is claimed. The same journeys *are* executed without a browser by
    `scripts/e2e_journeys.py`.
108. **The headless journeys start the real stack.** `scripts/e2e_journeys.py` migrates a database,
    bootstraps the demo tenant, starts a real Redis and **two** uvicorn replicas, then drives HTTP and
    two WebSockets: it asserts an unsigned webhook is refused, a signed one is accepted, the
    resulting event reaches both replicas, and a supplier reply is read by a real LLM over HTTP. It is
    in CI, so "it works across replicas" is a build artefact, not a claim.
109. **Templates are versioned, never edited in place.** `POST /templates` deactivates the previous
    version and inserts the next one; the UI offers "new version" rather than "edit". A message that
    was sent must always be explainable by the template text of its time.
110. **The extraction list exposes the model that produced it.** Reviewers can see whether a real
    provider or the deterministic fallback read a supplier's offer; without that field an operator
    cannot tell a degraded deployment from a normal one.
111. **The rate limiter is honest about being per-process.** Buckets live in memory, keyed by user id
    and address, which is correct for one replica and multiplies the effective budget by the replica
    count. It is documented (not silently "solved") because a Redis-backed limiter is the next change,
    and a fake distributed limiter would be worse than none.
112. **`doctor` is a gate, not a fortune cookie.** The command reports what the process actually
    resolved — database connectivity, migration head, extensions, every adapter's mode — and **exits
    non-zero** when a staging/production box still has an unconfigured or faked critical provider, so
    the runbook's first step is also a machine-checkable pre-flight. It never prints a secret value,
    only whether the variable is set, and it degrades to `connectivity: failed` instead of crashing
    when the database is unreachable — the report is needed most exactly then.
113. **The environment contract is a test, not a promise.** `docs/ENVIRONMENT.md` claiming "every
    variable" is worth nothing if it silently rots: `tests/test_env_docs.py` fails the build when a
    `Settings` field is undocumented, when `deploy/.env.example` declares a variable nothing reads, or
    when the template ships something that looks like a live credential. Adding a setting now requires
    documenting it in the same commit.
114. **Provider credentials live in the tenant's database, not in the process environment.** A new
    `integration_config` table (migration `0017_tenant_integrations`) holds one row per
    tenant × kind; secrets inside it are encrypted with the project's existing `EncryptedString`
    column, so a database dump never yields a plaintext token. The environment is still the bootstrap
    for the *platform's* own adapters (`LLM_PROVIDER`, `WHATSAPP_FROM`, …), but a tenant that saves
    credentials on the Integrations screen no longer needs a deployment change to go live.
115. **Resolution is tenant → legacy row → platform, in that order, and it is visible.**
    `services/integrations.configured()` only returns an integration when it is complete *and*
    enabled; `adapter_for()` then builds the real adapter, so a half-saved configuration can never
    silently fall through to a fake. `/integrations` reports both `provider_source`
    (`tenant` | `legacy` | `platform`) and `platform_provider`, which is how the UI can say "your key
    is in charge" instead of guessing. Disabling a row removes it from that precedence chain, which is
    exactly how "disable restores the safe mode" is implemented rather than special-cased.
116. **Partial credentials are an error state, not a sandbox state.** `snapshot()` recomputes `missing`
    from the catalogue on every read and reports `error` with the missing field names (and the
    vendor's `last_error` when a probe failed). The alternative — showing `Sandbox` for a save that
    cannot possibly work — is the failure mode this whole screen exists to remove.
117. **Secrets are write-only.** The API returns `secrets_saved` (the names) and `masked` (`••••` +
    the last four characters) and never the value; saving again without touching a field keeps the
    stored secret instead of blanking it, and `secrets_changed` tells the UI whether to say
    "updated". Every save, enable, disable and delete writes an audit entry with the field names (not
    the values), so a credential rotation is attributable after the fact.
118. **The vendor-side proof is a real socket, and it starts from the screen.** The integration tests
    and `scripts/e2e_journeys.py` start HTTP servers that speak the Twilio, Meta Cloud API, SerpAPI,
    Anthropic and OpenAI wire formats, and the e2e run keeps the *platform* on `fake` with no vendor
    secret in the environment. Any `live` status or vendor call in that run therefore has exactly one
    possible origin: the credentials PUT through `/integrations/{kind}`. A probe that only checks
    "the fields are non-empty" would have passed while the product was broken.
119. **Accounting is a deployment switch, not a tenant flag.** The pilot must not show a trace of it,
    so `ACCOUNTING_ENABLED` lives in `Settings` and is resolved when the router is assembled:
    `build_api_router(accounting_enabled=…)` simply does not include the accounting router, which is
    why the paths are absent from `/api/openapi.json` (verified in
    `tests/test_accounting_hidden.py`) rather than answering a 403 or a `BLOCK_FEATURE_OFF` that
    would advertise the module. A tenant administrator therefore cannot discover — let alone
    enable — it. The two supplier-ledger routes and the P2P lifecycle route moved out of `orders.py`
    into the accounting module for the same reason: leaving them behind would have kept four paths
    in the schema that only the accounting module can answer.
120. **The hidden module loses its permissions, not its matrix.** `core.rbac.visible_permissions()`
    removes `accounting:*`, `cost:*`, `ledger:read` and `audit_package:*` from the effective set at
    the one place `Context` is built, so `/auth/me`, the sidebar, every `require()` and every
    capability-driven card agree without a second source of truth. The RBAC matrix itself is
    untouched — flipping the switch restores the same roles.
121. **The auditor package is not assembled when accounting is off.** `receiving.confirm` used to seal
    a package on every delivery confirmation. Its purpose is to gate the invoice into accounting, and
    with the module off both the review route and the journal it lists are 404 — a bundle nobody can
    review and a PO parked at "ready for audit" forever. So the call is skipped, the receipt and the
    confirmation are unaffected, and the P2P rail (`p2p.stages()`) ends at "confirmed by requester".
    Recorded stage rows are never rewritten, so the history of a tenant that later enables accounting
    is intact.
122. **The pilot ships the AI-voice surfaces off.** `VOICE_AI_ENABLED` and `VOICE_ASSISTANT_ENABLED`
    default to `false` because the pilot plays a scripted transcript — there is no live audio path in
    the product today. Nothing was removed: the endpoints, the flags and their tests still exist, and
    an operator can turn them on; `tests/test_api_feature_flags.py` pins the shipped defaults so the
    claim in `docs/PROGRESS.md` cannot drift.

123. **The webhook resolves tenancy from the payload, never from a default tenant.** Meta cannot send
    custom headers, so `x-tenant-slug` defaulting to `nile` meant every WhatsApp webhook landed on the
    demo tenant. The tenant is now the one that registered the `phone_number_id` on the Integrations
    screen — the same id the Cloud adapter already sends from. An unregistered number is answered
    `200 {processed: 0}` (so Meta stops retrying a delivery nothing can accept), logged once, and
    recorded as `channel.unknown_recipient`; it is never guessed from a header. The header survives
    only as an explicit fallback for Twilio and local development, and strict deployments ignore the
    implicit default.
124. **A signature rejection is a recorded event, not a silent 401.** The response stays opaque
    (`{"error":"invalid signature"}`), but one log line names the reason (`missing_app_secret` versus
    `bad_signature`) and an audit row is written — without the secret and without the body. A strict
    deployment with `WHATSAPP_PROVIDER=cloud_api` now refuses to boot without `WHATSAPP_APP_SECRET`,
    because a Cloud deployment that cannot verify a single delivery is broken, not degraded.
125. **A colleague's message becomes a purchase request; a supplier's reply stays a quote.** The
    inbound router decides in this order: an active user whose profile phone matches *and* who is not
    currently invited on an open RFQ → `intake.intake_free_text` (the same service `/intake/text`
    uses), audited as `intake.whatsapp`, answered on WhatsApp with the code or the open clarification
    question; otherwise a supplier with an open invitation → the existing extraction pipeline;
    otherwise an unmatched event. The open invitation is the tiebreaker, because a supplier can also
    be a colleague and the paperwork — not the phone book — says which conversation this is.
126. **Numbers are normalised once, in one helper.** `services/phone.normalise` resolves the Egyptian
    local form (`01xxxxxxxxx`), E.164 (`+201…`), bare international (`201…`) and the `00` prefix to
    country code + national digits, so a phone typed by a human and a `wa_id` emitted by Meta compare
    equal. It is used for user matching and for supplier matching alike.
127. **The call screens say what the pilot actually does.** Both AI-voice switches already default to
    false, but the wording still promised live AI speech ("Start an AI call", "the AI disclosure is the
    first thing the supplier hears"). The pilot places no live audio: the call is a scripted transcript
    whose disclosure, turns and refusals are real records. The screen now carries one line saying so
    (Arabic first, English second), and the action reads "Start a call" / "بدء مكالمة". Nothing was
    deleted — the buttons, the endpoints and their request shapes are untouched, so an operator who
    turns the flags on gets exactly the phase-3 behaviour with honest labels.
128. **Demo data and the public demo password are refused outside dev.** `bootstrap --demo` and
    `seed-demo` are how a laptop gets a populated product; in staging or production they are a
    tenant full of accounts whose password is printed in the README. Both commands now refuse there
    (production already did; staging did not), and a strict deployment refuses to *start* while
    `BOOTSTRAP_DEMO_PASSWORD` is still the shipped default. Dev and test keep the one-command demo
    flow untouched, and `ALLOW_FAKE_PROVIDERS` does not excuse a published password — it excuses fake
    adapters, nothing else.
129. **The README describes what was tested, not what was wired.** The WhatsApp intake sentence claimed
    a channel that silently discarded every real delivery (fixed above) and listed AI voice calls with
    no mention that the pilot places none. The README now names the actual intake behaviour and adds
    one line about vendors being verified against local servers that speak the documented payloads,
    never against live APIs. This matters more than any feature: a reader makes deployment decisions
    from the README, and the first real vendor call is an integration test with a human watching.
