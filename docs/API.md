# ProcureAI API reference

Base URL: `/api/v1` (configurable via `API_PREFIX`). OpenAPI JSON: `/api/openapi.json`;
interactive docs: `/api/docs`.

## Conventions

* **Auth.** `Authorization: Bearer <access_token>`. Obtain it from `POST /api/v1/auth/login`
  (or `POST /api/v1/auth/mfa/verify` when MFA is enabled). Access tokens are short lived; rotate by
  `POST /api/v1/auth/refresh` with the refresh token. Public endpoints under `/public/*` ignore
  `Authorization` and authorise with a signed, expiring link token instead.
* **Tenancy.** The tenant is taken from the token. There is no tenant parameter on business routes.
* **Errors.** RFC 9457 `application/problem+json`:

  ```json
  {
    "type": "https://procureai.mantiq.tech/errors/forbidden",
    "title": "Forbidden",
    "status": 403,
    "detail": "Missing permission: negotiation:policy",
    "code": "forbidden",
    "request_id": "req_5cfae4201c0f4898",
    "instance": "/api/v1/rfqs/1f0e.../policy",
    "required_permission": "negotiation:policy"
  }
  ```

  Status codes used: `400` malformed, `401` unauthenticated, `403` missing permission,
  `404` not found **or not yours**, `409` illegal state transition, `422` validation/business rule,
  `429` rate limited, `503` dependency unavailable.
* **Pagination.** List endpoints accept `page` / `page_size` and return
  `{"items": [...], "total": n, "page": 1, "page_size": 20}`.
* **Idempotency.** Mutating agent/tool calls accept an `idempotency_key`; message sends are
  idempotent per (RFQ, supplier, round).
* **Request ids.** Send `x-request-id` to correlate; the server echoes it and stores it on audit rows.
* **Money.** Amounts are decimal strings (`"19500.0000"`), never floats. Currencies are ISO-4217.
* **Bilingual text.** Outbound message payloads carry `body` (English) and `body_ar` (Arabic).

## Lifecycle (the happy path)

```bash
# 1. intake
POST /api/v1/intake/text            {"text": "Need 30t rebar 16mm at 20500 EGP/t"}
GET  /api/v1/requests/{id}          # clarification questions
POST /api/v1/requests/{id}/clarifications/{qid}/answer  {"answer": "ASTM A615"}
POST /api/v1/requests/{id}/submit

# 2. RFQ
POST /api/v1/rfqs                   {"pr_id": "...", "supplier_ids": ["..."], "deadline_days": 7}
POST /api/v1/rfqs/{id}/dispatch
POST /api/v1/rfqs/{id}/simulate-reply   {"supplier_id": "...", "kind": "text", "unit_price": "20350"}

# 3. verification
GET  /api/v1/extractions?rfq_id=...
POST /api/v1/extractions/{id}/fields/{field_id}/verify   {"value": "20350"}
POST /api/v1/extractions/{id}/verify

# 4. comparison and recommendation
GET  /api/v1/rfqs/{id}/comparison
PUT  /api/v1/rfqs/{id}/weights      {"price": 0.6, "lead_time": 0.25, "quality": 0.15}
POST /api/v1/rfqs/{id}/recommend

# 5. negotiation (policy is mandatory)
PUT  /api/v1/rfqs/{id}/policy       {"target_price": "19500", "walkaway_price": "21000",
                                     "max_rounds": 3, "allowed_concessions": ["payment_terms"],
                                     "tone": "professional", "mode": "semi_auto",
                                     "value_threshold": "500000", "currency": "EGP"}
POST /api/v1/rfqs/{id}/negotiate    {"supplier_id": "...", "offer_id": "...", "round_no": 2}
POST /api/v1/negotiations/{id}/send

# 6. approval and award
POST /api/v1/rfqs/{id}/request-approval  {"offer_ids": ["..."], "amount": "1017500"}
GET  /api/v1/approvals?status=pending
POST /api/v1/approvals/{id}/decide  {"decision": "approve", "comment": "Within budget"}
GET  /api/v1/purchase-orders/{id}
GET  /api/v1/purchase-orders/{id}/pdf
```

## Post-award lifecycle (delivery → three-way match → settlement)

The award creates the purchase order. Everything after it is a *human* loop: the warehouse records
what arrived, the requester confirms it matches the need, and finance approves a matched invoice
before any payment is recorded. No endpoint here executes a payment.

```bash
# 1. delivery (po:update, then receipt:create)
POST /api/v1/purchase-orders/{po_id}/ship            ?carrier=Nile%20Logistics
POST /api/v1/purchase-orders/{po_id}/receipts        {"warehouse": "Alexandria central stores",
                                                      "delivery_note_ref": "DN-88510",
                                                      "carrier": "Nile Logistics",
                                                      "lines": [{"line_no": 1, "received_qty": "200",
                                                                 "accepted_qty": "200", "rejected_qty": "0"}]}
GET  /api/v1/purchase-orders/{po_id}/receipts

# 2. the requesting team signs off (receipt:confirm) — the warehouse cannot confirm its own receipt
POST /api/v1/receipts/{receipt_id}/confirm           {"decision": "confirm", "quantity_ok": true,
                                                      "quality_ok": true, "spec_ok": true,
                                                      "documents_ok": true, "comment": "Matches the spec"}

# 3. invoice + three-way match (invoice:create) — partial receipts stay invoiceable
POST /api/v1/purchase-orders/{po_id}/invoices        {"number": "INV-00001", "due_date": "2026-11-26",
                                                      "lines": [{"line_no": 1, "quantity": "200",
                                                                 "unit_price": "20100"}]}
POST /api/v1/invoices/{invoice_id}/match             # re-run the deterministic match

# 4. human approval for payment (invoice:approve); a mismatch needs an explicit override_reason
POST /api/v1/invoices/{invoice_id}/approve           {"comment": "Checked against the GR"}
POST /api/v1/invoices/{invoice_id}/approve           {"override_reason": "Supplier corrected the unit price"}

# 5. bookkeeping for a transfer executed in the bank/ERP (payment:record) — no money moves here
POST /api/v1/invoices/{invoice_id}/payments          {"amount": "2448400", "method": "bank_transfer",
                                                      "reference": "CIB-TRF-90211", "note": "First instalment"}

# 6. balances and closure
GET  /api/v1/supplier-balances
GET  /api/v1/suppliers/{supplier_id}/balance
POST /api/v1/purchase-orders/{po_id}/close           # only after the requester confirmed
```

**Status model.** `issued → in_transit → partially_received → received → confirmed → closed`.
`received` means the warehouse has the goods; `confirmed` means the requesting team accepted them.
Invoices are `matched | mismatched | approved | partially_paid | paid | disputed`.

**Match issue codes** (returned in `match_result.issues`, so a UI never has to guess):
`QTY_EXCEEDS_RECEIPT`, `PRICE_VARIANCE`, `NO_CONFIRMED_RECEIPT`, `UNKNOWN_LINE`,
`CURRENCY_MISMATCH`, `SUBTOTAL_MISMATCH`, `TOTAL_MISMATCH`, `INVALID_QUANTITY`. The default price
tolerance is 0.5% per line; over-invoicing beyond the confirmed quantity is always a mismatch.

**Payment suggestion.** `match_result` and `payment_suggestion` are computed deterministically from
the invoice, the supplier record and tenant policy (`bank_transfer | swift_wire | letter_of_credit`).
Every suggestion carries `requires_human_approval: true`, `execution: "outside_platform"` and a
reason list (`PREFERRED_SUPPLIER`, `OVERDUE`, `CROSS_BORDER_HIGH_VALUE`, …). There is no endpoint
that pays, and the agent has no tool that can reach one.

## Accounting (phase 2): accrual → posting → supplier ledger

The accounting module is the only place where a purchase becomes a number. Every endpoint below is
permission-gated (`accounting:read|write|post|reverse`, `cost:read|write`, `audit_package:read|review`)
and **no endpoint moves money**: `payment_method` on an invoice is a *suggestion*, not an execution.

```bash
# 0. the eleven-stage rail and its written history
GET  /api/v1/purchase-orders/{po_id}/lifecycle
#    {current, stages:[{code,label_en,label_ar,state,reached_at,actor_id}],
#     next_action_en/ar, history:[{stage,source,reached_at,actor_id}]}

# 1. the auditor package — assembled automatically when the requester confirms
GET  /api/v1/purchase-orders/{po_id}/audit-package
POST /api/v1/purchase-orders/{po_id}/audit-package          # re-assemble after new evidence
POST /api/v1/audit-packages/{package_id}/review              {"decision":"accept|query","comment":"…"}
#    package.verification.intact is recomputed on every read (SHA-256 manifest)

# 2. master data (accounting:write)
GET  /api/v1/accounting/accounts
POST /api/v1/accounting/accounts                             {"code":"550100","name":"…","name_ar":"…","kind":"expense"}
PATCH /api/v1/accounting/accounts/{code}
GET  /api/v1/accounting/cost-centers
POST /api/v1/accounting/cost-centers                         {"code":"CC-100","name":"…","name_ar":"…"}

# 3. the accrual at the receiving door (accounting:write)
POST /api/v1/purchase-orders/{po_id}/accounting/accrual       {"entry_date":"2026-10-31",
                                                               "allocations":[{"line_no":1,
                                                                               "cost_center_code":"CC-100",
                                                                               "unit_number":"APT-12"}]}
#    D 110100 inventory / C 110200 GR-IR at PO cost; only the not-yet-accrued increment

# 4. journal entries: draft → post → reverse (accounting:write / accounting:post / accounting:reverse)
GET  /api/v1/accounting/journal-entries?status=draft&source_type=receipt_accrual
POST /api/v1/accounting/journal-entries                       # manual entry (needs source_type="manual")
GET  /api/v1/accounting/journal-entries/{entry_id}
POST /api/v1/accounting/journal-entries/{entry_id}/post       # a human holding accounting:post
POST /api/v1/accounting/journal-entries/{entry_id}/reverse    {"reason":"Duplicate receipt accrual"}

# 5. the invoice posting — the three gates live here
POST /api/v1/invoices/{invoice_id}/posting                    {"entry_date":"2026-10-31",
                                                               "allocations":[{"line_no":1,"cost_center_code":"CC-100"}]}
POST /api/v1/invoices/{invoice_id}/payment-posting            {"payment_id":"…"}
#    422 match_not_clean → the invoice disagrees with the GR
#    422 invoice_not_approved → finance has not approved it
#    422 audit_review_required → the package is not accepted
#    409 duplicate_posting → an entry already exists for this invoice

# 6. reports (cost:read / accounting:read / ledger:read)
GET  /api/v1/accounting/trial-balance?as_of=2026-10-31
GET  /api/v1/accounting/cost-report?group_by=cost_center   # cost_center|item|unit|supplier|purchase_order
GET  /api/v1/purchase-orders/{po_id}/accounting            # every entry + cost split for one order
GET  /api/v1/accounting/supplier-account/{supplier_id}     # movements, ledger entries, outstanding
```

**Posting rules.** A receipt accrual debits inventory (`110100`) against GR/IR (`110200`) per line.
An invoice posting clears GR/IR at *PO* cost and books the difference to price variance (`530100`)
when an accrual exists, or debits inventory directly when it does not; VAT input (`120100`) is booked
when the invoice carries tax and accounts payable (`210100`) is credited at the invoice total. A
payment posting debits AP and credits bank (`100100`). System accounts are exempt from the
cost-centre requirement; every inventory or expense debit needs one (`missing_cost_center`).

**Immutability.** `journal_entry` rows are editable only while `draft`. `journal_line`,
`cost_allocation` and `p2p_stage_event` are append-only at the database level (`REVOKE UPDATE,
DELETE`), so a posted number cannot be rewritten behind the API — corrections are reversals.

**Error codes** (RFC 9457 `code`): `empty_entry`, `unknown_account`, `unbalanced` (with both totals),
`unknown_cost_center`, `missing_cost_center`, `duplicate_accrual`, `no_confirmed_receipt`,
`invoice_not_approved`, `match_not_clean`, `audit_review_required`, `segregation_of_duties`,
`already_posted`, `already_reversed`, `reversal_reason_required`.

## Channels, inbox, voice and assistant (phase 3)

**Channel resolution.** Every outbound supplier message asks `services/channels.resolve()`. The order
is: an RFQ-level override (if set) → the supplier's preferred channel → its secondary channels →
`whatsapp, voice, email, web_form`. A channel is only chosen when it is *reachable*: a phone on file
for WhatsApp/voice, an address for email, an active (non-blacklisted) supplier, an explicit opt-in for
WhatsApp/voice, and the tenant feature switch on. `GET /rfqs/{id}/channels` returns the resolved
channel, the reason it was chosen (`supplier_preference`, `secondary_preference`, `rfq_override`,
`fallback`), the per-channel reasons it was not (`no_consent`, `no_phone`, `feature_off`,
`supplier_blacklisted`) and the communication status (`pending → sent → delivered → read → replied`,
`no_response` after the deadline).

```bash
# Override one supplier on one RFQ (null clears the override)
curl -X PUT /api/v1/rfqs/$RFQ/suppliers/$SUPPLIER/channel \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"channel":"voice","reason":"Supplier asked for a call"}'

# Send a structured quote request (one interactive message: price, lead time, validity)
curl -X POST /api/v1/rfqs/$RFQ/whatsapp -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"supplier_id":"'$SUPPLIER'","quote_request":true,"locale":"ar"}'
```

**WhatsApp provider contract** (`app/adapters/whatsapp.py`): `send_text`, `send_template`,
`send_document`, `send_interactive(to, body, options, flow)`, `download_media(media_id)`,
`verify_webhook(body, signature)`, `parse_webhook`, `parse_statuses`, `parse_consent_events`.
The Cloud API adapter uses the WhatsApp Flow when `whatsapp_quote_flow_id` is configured and falls
back to reply buttons otherwise.

**Webhook.** `GET /api/v1/inbound/{channel}` answers Meta's verification handshake (echoes
`hub.challenge` when `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`). `POST /api/v1/inbound/whatsapp`
verifies `X-Hub-Signature-256` in `cloud_api` mode, then processes, in this order: delivery receipts →
consent events (`STOP` / `START`) → messages (media downloaded and transcribed/extracted) → the shared
extraction pipeline. The tenant is chosen with the `x-tenant-slug` header (default `nile`).

**Inbox.** `GET /inbox` groups messages by (RFQ, supplier) thread, not by channel, so a WhatsApp reply
appears next to the email that started the thread. `reply_channel` is the channel of the supplier's
last inbound message; `POST /inbox/threads/{id}/reply` sends there unless `channel` is passed. A
reply is refused (`channel_unavailable`) when consent or a reachable address is missing.

**AI voice.** `POST /voice/calls` with `mode=ai` runs: feature flag → tenant flag → country calling
hours → opt-in → dial → disclosure → consent → script → transcript → per-turn policy classification →
extraction into the ordinary human verification queue. `mode=human` returns a bilingual script for a
person to dial. `GET /voice/calls/{id}` returns the transcript, every policy turn with its
`block_code` and the policy snapshot stored on the call.

**Assistant.** `POST /assistant/ask` answers inside the caller's permissions only. Intents:
`help`, `document_status`, `my_requests`, `offer_summary`, `recommendation`, `supplier_balance`,
`supplier_communications`, `pr_create` (drafts a requisition; never submits), and `forbidden` for
approve/award/sign/pay. `POST /assistant/transcribe?ask=true` chains speech-to-text into the same
answer in one round trip. Every turn is stored and audited.

**Error and verdict codes.** WhatsApp sends return `200` with `ok: false` and a verdict rather than an
HTTP error — `BLOCK_NO_OPT_IN`, `BLOCK_FEATURE_OFF`, `BLOCK_CHANNEL_UNAVAILABLE` or `BLOCK_POLICY` — because the
refusal is evidence and must be stored. HTTP errors: `404` unknown supplier/RFQ/thread, `422`
`channel_unavailable` (with `reasons`) when an override targets an unreachable channel without a
reason, `403` for a missing permission. Assistant verdicts: `ASSISTANT_PERMISSION`,
`ASSISTANT_FORBIDDEN_ACTION`, `ASSISTANT_NOT_FOUND`. Voice policy codes: `COMPETITOR_DISCLOSURE`,
`ACCEPT_INTENT`, `SPEC_CHANGE`, `SIGN_INTENT`, `PAYMENT_EXECUTION`.

## Endpoint inventory

The table below is generated from the live OpenAPI schema of the running application
(207 operations).

| Method | Path | Summary | Tag |
| --- | --- | --- | --- |
| GET | `/api/v1/accounting/accounts` | Chart of accounts | accounting |
| POST | `/api/v1/accounting/accounts` | Add a GL account | accounting |
| PATCH | `/api/v1/accounting/accounts/{code}` | Rename or deactivate a GL account | accounting |
| GET | `/api/v1/accounting/cost-centers` | Cost centres (analytical dimension) | accounting |
| POST | `/api/v1/accounting/cost-centers` | Create a cost centre | accounting |
| PATCH | `/api/v1/accounting/cost-centers/{code}` | Update a cost centre | accounting |
| GET | `/api/v1/accounting/cost-report` | Posted cost by cost centre, item, unit or supplier | accounting |
| GET | `/api/v1/accounting/coverage` | Is the tenant's accounting set up? | accounting |
| GET | `/api/v1/accounting/journal-entries` | Journal entries with filters | accounting |
| POST | `/api/v1/accounting/journal-entries` | Draft a manual journal entry | accounting |
| GET | `/api/v1/accounting/journal-entries/{entry_id}` | Journal entry detail | accounting |
| POST | `/api/v1/accounting/journal-entries/{entry_id}/post` | Post a draft to the ledger | accounting |
| POST | `/api/v1/accounting/journal-entries/{entry_id}/reverse` | Reverse a posted entry | accounting |
| GET | `/api/v1/accounting/supplier-account/{supplier_id}` | Supplier sub-ledger with journal support | accounting |
| GET | `/api/v1/accounting/trial-balance` | Trial balance over posted entries | accounting |
| GET | `/api/v1/agent-runs` | Agent activity feed (prompt/tool/reasoning audit) | admin |
| GET | `/api/v1/agent-runs/{run_id}` | Agent run detail with steps and tool calls | admin |
| POST | `/api/v1/agent/run` | Run the orchestration graph (stops at human-interrupt nodes) | admin |
| GET | `/api/v1/agent/tools` | Agent tool registry (no accept/sign/pay tools exist) | admin |
| GET | `/api/v1/analytics/export` | Export the analytics workbook (Excel) | admin |
| GET | `/api/v1/analytics/forecast` | Explainable forecast (seasonal naive + exponential smoothing) | admin |
| GET | `/api/v1/analytics/kpis` | Savings, cycle time and response-rate KPIs | admin |
| GET | `/api/v1/analytics/savings-by-buyer` | Savings / spend by buyer | admin |
| GET | `/api/v1/analytics/spend` | Spend by category, plant and supplier | admin |
| GET | `/api/v1/analytics/trends` | Item price trend series | admin |
| GET | `/api/v1/api-keys` | List API keys (hashed at rest) | admin |
| POST | `/api/v1/api-keys` | Create an API key (shown once) | admin |
| DELETE | `/api/v1/api-keys/{key_id}` | Revoke an API key | admin |
| GET | `/api/v1/approvals` | Approval inbox | rfq |
| POST | `/api/v1/approvals/escalate` | Run SLA escalation for overdue approvals | rfq |
| POST | `/api/v1/approvals/{approval_id}/decide` | Approve / reject / request revision / split award | rfq |
| POST | `/api/v1/approvals/{approval_id}/delegate` | Delegate an approval | rfq |
| POST | `/api/v1/assistant/ask` | Ask the internal assistant (read-only, RBAC-scoped) | channels |
| GET | `/api/v1/assistant/sessions` | My recent assistant conversations | channels |
| GET | `/api/v1/assistant/sessions/{session_id}` | One assistant conversation, turn by turn | channels |
| POST | `/api/v1/assistant/transcribe` | Transcribe a spoken question (STT) without trusting it | channels |
| GET | `/api/v1/audit` | Append-only audit trail | admin |
| POST | `/api/v1/audit-packages/{package_id}/review` | Auditor accepts the evidence or raises a query | accounting |
| POST | `/api/v1/auth/change-password` | Change password (enforces the password policy) | identity |
| POST | `/api/v1/auth/login` | Email + password sign-in (TOTP challenge when enabled) | identity |
| POST | `/api/v1/auth/logout` | Revoke the current refresh session | identity |
| GET | `/api/v1/auth/me` | Current user, tenant, permissions and preferences | identity |
| PATCH | `/api/v1/auth/me` | Update profile and preferences (locale, currency, contacts) | identity |
| POST | `/api/v1/auth/mfa/disable` | Disable MFA (requires a valid code) | identity |
| POST | `/api/v1/auth/mfa/enable` | Confirm TOTP enrolment (returns one-time backup codes) | identity |
| POST | `/api/v1/auth/mfa/setup` | Start TOTP enrolment (returns otpauth URI + secret) | identity |
| POST | `/api/v1/auth/mfa/verify` | Complete sign-in with a TOTP or backup code | identity |
| POST | `/api/v1/auth/password-reset/confirm` | Complete a password reset | identity |
| POST | `/api/v1/auth/password-reset/request` | Request a password reset link (no enumeration) | identity |
| POST | `/api/v1/auth/refresh` | Rotate the refresh token (single use, CSRF protected) | identity |
| GET | `/api/v1/auth/sessions` | List active sessions (devices) | identity |
| DELETE | `/api/v1/auth/sessions/{session_id}` | Revoke a device session | identity |
| GET | `/api/v1/auth/sso/config` | SSO availability for the tenant (SAML / OIDC) | identity |
| GET | `/api/v1/benchmark` | Price history and anonymised benchmark for an item | rfq |
| GET | `/api/v1/billing/invoices` | Invoices | admin |
| POST | `/api/v1/billing/invoices` | Generate the current period invoice | admin |
| GET | `/api/v1/billing/plans` | Subscription plans | admin |
| GET | `/api/v1/billing/usage` | Usage metering against plan limits | admin |
| GET | `/api/v1/billing/usage-export` | Export usage events (CSV) | admin |
| GET | `/api/v1/categories` | List tenant categories | intake |
| GET | `/api/v1/channels/capabilities` | Available supplier channels and their state | channels |
| POST | `/api/v1/comments` | Comment on an RFQ/PR with @mentions | admin |
| GET | `/api/v1/comments` | List comments for an RFQ or PR | admin |
| GET | `/api/v1/dashboard` | Dashboard payload (KPIs, spend, trends, forecast) | admin |
| GET | `/api/v1/data-export` | Data-subject export (JSON bundle) | admin |
| GET | `/api/v1/erasure-requests` | List erasure requests | admin |
| POST | `/api/v1/erasure-requests` | GDPR / PDPL right-to-erasure request | admin |
| POST | `/api/v1/erasure-requests/{request_id}/execute` | Execute erasure (PII purge, within 30 days) | admin |
| POST | `/api/v1/erp/import` | Import requisitions from the ERP connector | intake |
| GET | `/api/v1/erp/test` | Test the configured ERP connector | intake |
| GET | `/api/v1/extractions` | Verification queue (low-confidence extractions first) | rfq |
| GET | `/api/v1/extractions/{extraction_id}` | Extraction detail with per-field provenance | rfq |
| POST | `/api/v1/extractions/{extraction_id}/fields/{field_id}/verify` | One-click field verification | rfq |
| POST | `/api/v1/extractions/{extraction_id}/request-missing-info` | Ask the supplier for missing data | rfq |
| POST | `/api/v1/extractions/{extraction_id}/verify` | Verify the whole extraction and unlock comparison | rfq |
| POST | `/api/v1/inbound/{channel}` | Inbound webhook (email/WhatsApp) with signature verification | rfq |
| GET | `/api/v1/inbox` | Unified conversation inbox across every channel | channels |
| GET | `/api/v1/inbox/search` | Search every message on every channel | channels |
| GET | `/api/v1/inbox/threads/{thread_id}` | One conversation across all channels | channels |
| GET | `/api/v1/inbox/threads/{thread_id}/events` | The channel event trail of one thread | channels |
| POST | `/api/v1/inbox/threads/{thread_id}/reply` | Reply on the channel the supplier used | channels |
| POST | `/api/v1/intake/email` | Email-to-PR (inbound address / forwarded mail) | intake |
| POST | `/api/v1/intake/excel/confirm` | Confirm mapping and create the requisition | intake |
| POST | `/api/v1/intake/excel/preview` | Upload Excel/CSV and preview auto-mapping + validation | intake |
| GET | `/api/v1/intake/inbound-address` | Per-tenant inbound email address | intake |
| POST | `/api/v1/intake/scan` | Scanned / photographed request via OCR | intake |
| POST | `/api/v1/intake/text` | Create a requisition from free text (AR/EN) | intake |
| POST | `/api/v1/intake/voice` | Voice request via the STT adapter | intake |
| GET | `/api/v1/integrations` | Integration configuration (secrets masked) | admin |
| POST | `/api/v1/integrations` | Configure an integration (secrets envelope-encrypted) | admin |
| POST | `/api/v1/integrations/{kind}/test` | Test an integration | admin |
| POST | `/api/v1/invoices/{invoice_id}/approve` | Human approval of a matched invoice | orders |
| POST | `/api/v1/invoices/{invoice_id}/match` | Re-run the three-way match | orders |
| POST | `/api/v1/invoices/{invoice_id}/payment-posting` | Draft the AP settlement for a recorded payment | accounting |
| POST | `/api/v1/invoices/{invoice_id}/payments` | Record a payment executed outside the platform | orders |
| POST | `/api/v1/invoices/{invoice_id}/posting` | Draft the invoice posting (GR/IR clearing, VAT, variance, payable) | accounting |
| GET | `/api/v1/limits` | Approval limits per role and category (FR-AUTH-3) | identity |
| POST | `/api/v1/limits` | Upsert an approval limit | identity |
| GET | `/api/v1/llm-config` | Per-tenant LLM provider (including self-hosted) | admin |
| PUT | `/api/v1/llm-config` | Set the tenant's LLM provider | admin |
| GET | `/api/v1/meta/capabilities` | Which adapters are live vs Fake (owner action list) | admin |
| POST | `/api/v1/negotiations/{message_id}/send` | Send an approved draft (re-validated at send time) | rfq |
| GET | `/api/v1/notifications` | Notification centre | admin |
| POST | `/api/v1/notifications/read-all` | Mark all notifications read | admin |
| POST | `/api/v1/notifications/{notification_id}/read` | Mark one notification read | admin |
| GET | `/api/v1/onboarding/checklist` | First-run checklist state | admin |
| GET | `/api/v1/permissions` | All permissions known to the platform | identity |
| GET | `/api/v1/plants` | List plants | intake |
| POST | `/api/v1/plants` | Create a plant | intake |
| GET | `/api/v1/platform/health` | Platform health summary | admin |
| POST | `/api/v1/platform/impersonate` | Time-boxed, audited impersonation | admin |
| GET | `/api/v1/platform/tenants` | All tenants with health and usage | admin |
| GET | `/api/v1/public/form/{token}` | Public quote form payload (signed link, no login) | admin |
| POST | `/api/v1/public/form/{token}` | Public quote submission | admin |
| POST | `/api/v1/public/form/{token}/upload` | Attach a file to a public quote | admin |
| GET | `/api/v1/public/supplier/{token}` | Supplier self-service profile page | admin |
| POST | `/api/v1/public/supplier/{token}` | Supplier profile confirmation / catalogue upload | admin |
| GET | `/api/v1/purchase-orders` | List purchase orders | orders |
| GET | `/api/v1/purchase-orders/{po_id}` | Purchase order detail with the delivery lifecycle | orders |
| GET | `/api/v1/purchase-orders/{po_id}/accounting` | Postings and cost allocation for an order | accounting |
| POST | `/api/v1/purchase-orders/{po_id}/accounting/accrual` | Draft the GR/IR accrual for a confirmed receipt | accounting |
| GET | `/api/v1/purchase-orders/{po_id}/audit-package` | Current auditor package (if any) | accounting |
| POST | `/api/v1/purchase-orders/{po_id}/audit-package` | Assemble and seal the auditor package | accounting |
| POST | `/api/v1/purchase-orders/{po_id}/close` | Close a fully confirmed purchase order | orders |
| GET | `/api/v1/purchase-orders/{po_id}/excel` | Download the PO as Excel | orders |
| POST | `/api/v1/purchase-orders/{po_id}/export-erp` | Push the PO to the ERP connector | orders |
| GET | `/api/v1/purchase-orders/{po_id}/invoices` | Supplier invoices for a purchase order | orders |
| POST | `/api/v1/purchase-orders/{po_id}/invoices` | Record a supplier invoice and run the three-way match | orders |
| GET | `/api/v1/purchase-orders/{po_id}/lifecycle` | Eleven-stage purchase-to-pay status | accounting |
| GET | `/api/v1/purchase-orders/{po_id}/pdf` | Download the PO as PDF | orders |
| GET | `/api/v1/purchase-orders/{po_id}/receipts` | Goods receipts for a purchase order | orders |
| POST | `/api/v1/purchase-orders/{po_id}/receipts` | Record a goods receipt at the warehouse | orders |
| POST | `/api/v1/purchase-orders/{po_id}/ship` | Mark the purchase order as shipped by the supplier | orders |
| POST | `/api/v1/receipts/{receipt_id}/confirm` | Requester confirms the received items match the need | orders |
| GET | `/api/v1/requests` | List purchase requisitions | intake |
| GET | `/api/v1/requests/{pr_id}` | Requisition detail with lines and open questions | intake |
| POST | `/api/v1/requests/{pr_id}/assign` | Route a requisition to a procurement owner | intake |
| POST | `/api/v1/requests/{pr_id}/clarifications/{question_id}/answer` | Answer or waive a clarification | intake |
| POST | `/api/v1/requests/{pr_id}/lines/{line_id}/normalize` | Confirm/correct item normalization | intake |
| POST | `/api/v1/requests/{pr_id}/submit` | Mark a requisition ready for RFQ creation | intake |
| GET | `/api/v1/retention` | Data retention policies | admin |
| PUT | `/api/v1/retention` | Set a retention policy | admin |
| GET | `/api/v1/rfqs` | List RFQs | rfq |
| POST | `/api/v1/rfqs` | Create an RFQ from a requisition | rfq |
| GET | `/api/v1/rfqs/board` | Kanban board grouped by stage with deadline chips | rfq |
| PATCH | `/api/v1/rfqs/{rfq_id}` | Update RFQ settings / move stage on the board | rfq |
| GET | `/api/v1/rfqs/{rfq_id}` | RFQ detail (lines, invitations, offers, messages, policy) | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/channels` | Resolved channel and communication status per supplier | channels |
| POST | `/api/v1/rfqs/{rfq_id}/close` | Close an RFQ | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/comparison` | Offer comparison matrix with TCO and weighted scores | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/dispatch` | Send the RFQ to invited suppliers (email/WhatsApp/web link) | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/manual-script` | Prepared script for switching a call to manual | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/messages` | Thread timeline for an RFQ (all suppliers) | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/messages` | Send a manual message to a supplier | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/negotiate` | Draft (and optionally send) a policy-checked counter-offer | rfq |
| PUT | `/api/v1/rfqs/{rfq_id}/policy` | Create a versioned negotiation policy | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/policy` | Active negotiation policy | rfq |
| GET | `/api/v1/rfqs/{rfq_id}/preview` | Template preview (AR/EN) before sending | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/recommend` | Top-3 recommendation with traceable justification | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/reminders` | Run the reminder cadence for this RFQ | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/request-approval` | Start the multi-level approval chain | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/simulate-reply` | Inject a supplier reply through the real parsing pipeline | rfq |
| PUT | `/api/v1/rfqs/{rfq_id}/suppliers/{supplier_id}/channel` | Override the channel for one supplier on this RFQ (null clears it) | channels |
| POST | `/api/v1/rfqs/{rfq_id}/suppliers/{supplier_id}/switch-channel` | Move the live conversation to another channel | channels |
| GET | `/api/v1/rfqs/{rfq_id}/suppliers/{supplier_id}/timeline` | Negotiation timeline + reasoning | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/voice-calls` | Start an AI supplier call (disclosure + consent) | rfq |
| PUT | `/api/v1/rfqs/{rfq_id}/weights` | Set comparison weights for this RFQ | rfq |
| POST | `/api/v1/rfqs/{rfq_id}/whatsapp` | Send a rich WhatsApp message to a supplier | channels |
| GET | `/api/v1/roles` | Role catalogue with the permission matrix (UI mirror of RBAC) | identity |
| GET | `/api/v1/settings` | Tenant settings | admin |
| PATCH | `/api/v1/settings` | Update tenant settings | admin |
| GET | `/api/v1/settings/features` | Tenant feature flags for channels and AI voice | channels |
| PATCH | `/api/v1/settings/features` | Turn channel / AI-voice features on or off | channels |
| GET | `/api/v1/supplier-balances` | Outstanding balance per supplier | orders |
| GET | `/api/v1/suppliers` | List/search suppliers | intake |
| POST | `/api/v1/suppliers` | Create a supplier | intake |
| POST | `/api/v1/suppliers/discovery` | Run autonomous supplier discovery | intake |
| POST | `/api/v1/suppliers/discovery/approve` | Buyer approves discovered candidates (gate before contact) | intake |
| POST | `/api/v1/suppliers/discovery/reject` | Reject discovered candidates | intake |
| GET | `/api/v1/suppliers/discovery/{job_id}` | Discovery job results | intake |
| POST | `/api/v1/suppliers/import` | Bulk import suppliers from Excel/CSV | intake |
| GET | `/api/v1/suppliers/{supplier_id}` | Supplier detail with contacts (PII decrypted for authorised users) | intake |
| GET | `/api/v1/suppliers/{supplier_id}/balance` | Ledger and balance for one supplier | orders |
| POST | `/api/v1/suppliers/{supplier_id}/certifications` | Add a certification | intake |
| GET | `/api/v1/suppliers/{supplier_id}/channels` | Channel preferences and reachability | channels |
| PATCH | `/api/v1/suppliers/{supplier_id}/channels` | Set the preferred and secondary channels | channels |
| POST | `/api/v1/suppliers/{supplier_id}/flags` | Blacklist / prefer / approve a supplier | intake |
| POST | `/api/v1/suppliers/{supplier_id}/opt-in` | Record consent for one channel | channels |
| POST | `/api/v1/suppliers/{supplier_id}/portal-link` | Signed, expiring supplier self-service link | intake |
| POST | `/api/v1/suppliers/{supplier_id}/ratings` | Record supplier rating inputs | intake |
| POST | `/api/v1/suppliers/{supplier_id}/whatsapp/document` | Send a document to a supplier over WhatsApp | channels |
| GET | `/api/v1/taxonomy` | UNSPSC-based taxonomy (searchable) | intake |
| GET | `/api/v1/templates` | Message templates (per channel and locale) | rfq |
| POST | `/api/v1/templates` | Create or version a template | rfq |
| GET | `/api/v1/templates/library` | Templates per channel and locale, with their variables | channels |
| GET | `/api/v1/unit-conversions` | Unit conversion table used by normalization | intake |
| GET | `/api/v1/users` | List tenant users | identity |
| POST | `/api/v1/users` | Create a tenant user | identity |
| PATCH | `/api/v1/users/{user_id}` | Update a tenant user | identity |
| GET | `/api/v1/voice-calls/{call_id}` | Voice call console state | rfq |
| POST | `/api/v1/voice/calls` | Place an outbound AI call (disclosure + consent) | channels |
| GET | `/api/v1/voice/calls` | Call log for an RFQ (or the whole tenant) | channels |
| GET | `/api/v1/voice/calls/{call_id}` | Call console: transcript, policy turns and extraction | channels |
| GET | `/api/v1/voice/features` | Which voice features are enabled for this tenant | channels |
| GET | `/health` | Liveness probe | system |
| GET | `/metrics` | Prometheus-format metrics (minimal) | system |
| GET | `/ready` | Readiness probe (checks the database) | system |

## WebSocket

`GET /ws?token=<access_token>` upgrades to a tenant-scoped stream. Events:

| Event | Payload | Emitted by |
| --- | --- | --- |
| `connected` | `{tenant_id}` | on accept |
| `pong` | `{}` | reply to a client `ping` text frame |
| `offer.extracted` | `{rfq_id, supplier_id, extractions, offers}` | `app/services/extraction.py` |
| `notification.created` | `{kind, title, link, severity}` | `app/services/common.py` |

Every frame is JSON with a `type` field; `event` is kept alongside it so older subscribers keep
working. The upgrade is accepted **before** the token is validated, so a bad token produces a real
close code instead of an opaque handshake failure:

| Close code | Reason | Client behaviour |
| --- | --- | --- |
| `4401` | `invalid_token` | stop and wait for a new session (do not retry in a loop) |
| `4403` | `no_tenant` | stop; the token is not tenant-scoped |
| `1000` | normal close (tab closed, logout) | reconnect on next mount |

Clients send a bare `ping` text frame every 25 s (a JSON `{"type":"ping"}` frame is also accepted)
and treat a missing `pong` as a dead socket. The SPA reconnects with capped exponential backoff
(1/2/4/8/15/30 s) plus jitter, and immediately when the tab becomes visible again — the socket is a
convenience on top of the polling queries, never a dependency.

The hub is in-process (one room per tenant, `GET /metrics`-friendly `hub.connection_count()`).
Behind multiple API replicas run a single WS-serving deployment or add a Redis pub/sub fan-out —
that is the documented scale-out point. `scripts/load_test.py` exercises 42 concurrent sockets.

## Pagination, filtering and sorting

| Endpoint | Filters |
| --- | --- |
| `GET /rfqs` | `status`, `q`, `page`, `page_size`, `sort` |
| `GET /rfqs/board` | none — returns stages with counts |
| `GET /extractions` | `rfq_id`, `status` |
| `GET /approvals` | `status` |
| `GET /purchase-orders` | none (tenant scoped) |
| `GET /suppliers` | `q`, `category`, `page`, `page_size` |
| `GET /analytics/*` | `days` |

## Rate limits

`RATE_LIMIT_PER_MINUTE` (default 240) per client IP, enforced by middleware with a Redis counter
when Redis is configured and an in-process counter otherwise. Exceeding it returns `429` with a
`retry-after` header.

## Versioning

The path prefix is the version (`/api/v1`). Breaking changes ship as `/api/v2`; additive fields
never break clients. Deprecations are announced in `/api/openapi.json` (`deprecated: true`) one
minor release before removal.
