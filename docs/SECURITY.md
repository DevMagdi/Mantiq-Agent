# Security & privacy

This document is the security contract for ProcureAI. It describes the threat model, the
controls that exist in code today, and what an operator must do to keep them true.

## 1. Threat model

| # | Threat | Impact | Primary control |
| --- | --- | --- | --- |
| T1 | Cross-tenant data access through an id in a URL or payload | Critical | `tenant_id` scoping in every query, RLS on PostgreSQL, `404` (not `403`) for foreign ids |
| T2 | Prompt injection inside a supplier email/PDF/WhatsApp message | Critical | Content fencing, invisible-char stripping, phrase scoring, 40-payload corpus, "data not instructions" framing, no tool call driven by supplier text |
| T3 | Autonomous commitment (accept/sign/pay) by the agent | Critical | No such tool exists (`FORBIDDEN_TOOL_NAMES` assert), policy engine blocks `FORBIDDEN_INTENTS`, human approval chain mandatory, PO only after final approval |
| T4 | Competitor price/identity leaked in a negotiation message | High | `competitor_references()` check on every draft, `BLOCK_COMPETITOR_DISCLOSURE` code, benchmark hint is bucketed and anonymous |
| T5 | Credential stuffing / brute force | High | Argon2id, 5-attempt lockout for 15 minutes, per-IP rate limit, MFA support, audit trail |
| T6 | Session theft | High | Short-lived access token, single-use rotating refresh token bound to `refresh_session.jti`, revocable sessions list |
| T7 | PII exfiltration via a database dump or log file | High | Per-tenant envelope encryption for contact PII, structured logs redact identifiers, secrets never logged |
| T8 | SSRF through "fetch this supplier page" | Medium | `PageFetcher.is_public_url` rejects non-http(s), localhost, private ranges, IPv4 literals, cloud metadata; `robots.txt` respected |
| T9 | Malicious file upload | Medium | size cap (`max_upload_mb`), type-routed extraction, content stored outside the web root |
| T10 | Tampered audit trail | Medium | append-only table, hash chain, DB trigger blocks UPDATE/DELETE (migration 0010) |
| T11 | Open public link used as an enumeration oracle | Medium | 32+ byte random tokens, single purpose, expiring, RFQ-status aware, all submissions land in review |
| T12 | Unbounded LLM cost / runaway loops | Medium | per-tenant metering, `usage_event`, round caps, max 20k chars per reply, per-task token budgets |

## 2. Authentication & authorisation

* **Passwords**: Argon2id via `app/core/security.py`, minimum length/character classes, common
  password list, and a check that rejects the e-mail local-part inside the password.
* **Bootstrap**: `procureai bootstrap --demo` creates the first superadmin with a generated
  password, `must_change_password = true`, and prints the MFA provisioning URI once.
* **MFA**: TOTP (RFC 6238) with backup codes; enrolled secret is encrypted at rest.
* **Sessions**: access token TTL from `ACCESS_TOKEN_TTL_MINUTES`, refresh TTL in days, rotation on
  every refresh (a replayed token is rejected), `POST /auth/logout` revokes, `GET /auth/sessions`
  lists active devices and `DELETE /auth/sessions/{session_id}` revokes one.
* **Authorisation**: one dependency, `require(permission)`, resolves the tenant context and checks
  `has_permission(roles, permission, is_superadmin)`. It is impossible to add a route without
  naming a permission — the dependency is mandatory in the signature.
* **Tenant context**: derived from the token, never from the request body. `x-tenant-slug` is only
  honoured on the public/inbound webhook routes, where it selects the tenant whose *supplier*
  traffic is being received, and an unknown slug returns `404`.

## 3. Data protection

| Data class | Where | Protection |
| --- | --- | --- |
| User credentials | `user.hashed_password` | Argon2id, never logged, never exported |
| MFA secret | `user.totp_secret` | encrypted with the tenant key |
| Supplier contacts | `supplier_contact.{name,email,phone}` | per-tenant envelope encryption, `key_id` recorded |
| Messages | `channel_message.body` | TLS in transit; stored for audit; redacted on erasure when the subject identifier appears |
| Commercial records | RFQs, offers, POs | retained per retention policy; never silently deleted |
| Audit log | `audit_log` | append-only + hash chain |
| Files | object storage | private bucket, signed URLs, no public ACL |

**Data-subject rights** (`app/services/privacy.py`):

* `POST /admin/data-export` returns a machine-readable bundle (`export_subject_data`) containing the
  user record, decrypted contact records, suppliers, message envelopes and retention notes.
* `POST /admin/erasure-requests` opens a request with a 30-day `due_at`;
  `POST /admin/erasure-requests/{id}/execute` redacts PII columns (name, e-mail, phone), clears
  opt-ins, scrubs message bodies that quote the subject, disables the user account, keeps the
  transaction skeleton, and writes `privacy.erasure_executed` to the audit log.
* Erasure is idempotent: re-running reports zero purges.

## 4. AI-specific controls

1. **Tool surface is closed.** Twelve tools, each permission-gated and audited. The forbidden verbs
   are asserted absent at import time; an attempted call returns a refusal with no side effect.
2. **No tool can commit.** `create_po_for_offer` is reachable only from the approval service after a
   human decision; there is no tool for it.
3. **Policy engine is deterministic.** No model output can change a `BLOCK_*` decision; models only
   contribute text and a proposed price, which is re-checked against walk-away/target.
4. **Number guard.** Any figure in generated prose that is not in the computed ranking is treated as
   fabricated and replaced by a deterministic bilingual sentence.
5. **Untrusted content never becomes instructions.** Supplier text is fenced, stripped of invisible
   characters, scored, and passed as data. Injection attempts raise `PROMPT_INJECTION_SUSPECTED`
   and force human review.
6. **Voice calls disclose first.** `start_call` always sends the country policy's disclosure, records
   only with consent, discards the recording URI when consent is refused, and refuses to dial
   outside the destination's permitted hours or without an opt-in.
7. **Replay safety.** Tool calls carry an `idempotency_key`; message sends are idempotent per
   (rfq, supplier, round).

## 5. Transport & headers

`app/main.py` middleware sets `x-request-id`, `x-content-type-options: nosniff`,
`referrer-policy: strict-origin-when-cross-origin`, `x-frame-options: DENY`,
`permissions-policy: geolocation=(), microphone=(), camera=()`, and in production HSTS plus a
strict CSP. CORS is an explicit allow-list (`CORS_ORIGINS`), never `*` with credentials.

## 6. Secrets management

* Configuration is environment-only (`app/core/config.py`, Pydantic settings). No secret has a
  default in production; `SECRET_KEY` must be 32+ characters and is validated at startup.
* `ENCRYPTION_MASTER_KEY` wraps a per-tenant data key (DEK). Each tenant stores
  `{dek_id, wrapped_dek}` in `tenant.settings`, and every ciphertext records its `key_id`, so a DEK
  can be re-wrapped under a new master key without re-encrypting rows. Rotation procedure: wrap the
  existing DEKs with the new master key in a one-off migration script while both keys are available
  in the environment, then remove the old one. (There is deliberately no ad-hoc `rotate-keys` CLI
  yet — it is a documented extension point.)
* `.env` is git-ignored; `deploy/.env.example` documents every variable with no real values.
* Adapters are only constructed when their credentials are present; otherwise the deterministic
  fake is used, so a missing secret degrades functionality instead of leaking or crashing.

## 7. Logging & monitoring

Structured JSON logs include `request_id`, `route`, `status_code`, `latency_ms`, tenant and actor
ids — never bodies, tokens, passwords, TOTP codes or full contact PII. `app/core/logging.py::redact()`
masks sensitive keys (password, token, secret, TOTP, authorization) before a record is emitted.
Security events (login failure, lockout, MFA failure, policy block, erasure) are always audited.

## 8. Operator checklist (before production)

1. Set `APP_ENV=production`, a strong `SECRET_KEY` and `ENCRYPTION_MASTER_KEY`.
2. Use PostgreSQL 16 with the extensions from migration `0001`; confirm RLS policies exist
   (`\d+ rfq` shows `relrowsecurity = t`) and that the app role is not a superuser.
3. Enable MFA for every `superadmin`/`admin` (enforced in the UI flow) and keep `REQUIRE_MFA=true`
   for environments that must refuse password-only sessions.
4. Put the API behind TLS termination; restrict `/api/docs` to an internal network.
5. Configure real adapter credentials (email, WhatsApp, telephony, search, ERP, payment).
6. Set `OTEL_ENABLED=true` and `SENTRY_DSN` to ship traces/errors; alert on `policy_block` spikes, login
   failure bursts and `agent_run.status == failed`.
7. Schedule `procureai purge` (retention + pending erasures) and the reminder/escalation jobs on
   `celery beat`.
8. Run `procureai doctor` and confirm every adapter reports configured/fake as intended.

## 9. Responsible disclosure

Report vulnerabilities to `security@mantiq.tech` with reproduction steps. Do not open a public
issue. Acknowledgement within 2 business days; a fix window agreed per severity.
