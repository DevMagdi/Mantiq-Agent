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
4. **Adapters always ship with a fake.** `registry.resolve(...)` returns the real client only when
   its credentials exist, otherwise the deterministic fake. This is what makes a 464-test suite
   hermetic and what lets a demo run with zero external accounts.
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
