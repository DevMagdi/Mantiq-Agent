# Phase 2 — full purchase-to-pay lifecycle and a real accounting module

Branch: `arena/acf2531d-mantiq-agent` (Arena binds this session to that branch; `git checkout -b` is
blocked in the sandbox, so the work is delivered from the session branch, as agreed in phase 1).

This PR closes the phase-2 scope end to end: the eleven-stage P2P status machine, the automatic
auditor document package, a mandatory three-way match that gates the books, and an accounting module
that produces double-entry journal entries, a supplier sub-ledger and analytical cost — with the
human, never the agent, in control of every posting.

Nothing in this PR executes a payment, accepts an offer, or signs anything. No AI-voice work and no
advanced channel selection was done (both are explicitly out of scope for this phase).

---

## 1. The purchase-to-pay status machine

| Requirement | How it is met |
| --- | --- |
| Eleven exact stages | `app/services/p2p.py` → `STAGES`: `requested, in_procurement, rfq_sent, offers_received, under_approval, po_issued, pending_receipt, received_by_warehouse, confirmed_by_requester, ready_for_audit, posted_to_accounts` |
| A stage cannot be "set" by hand | `derive_stage` computes the stage from the documents (requisition, RFQ, offers, approval steps, PO, receipts, confirmation, package, postings). If the papers do not support a stage, the rail does not show it |
| History that cannot be rewritten | `p2p_stage_event` is append-only (`REVOKE UPDATE, DELETE` for the app role); `record_stage`/`touch` write the first arrival of each stage with actor + source action |
| Requester creates, procurement owns | `POST /requests/{id}/assign` routes a requisition to a named buyer with `department` + `assigned_at`; the rail moves to `in_procurement` |
| RFQ → offers → approval → PO | Existing procurement flow, now observed: `rfq_sent` on dispatch, `offers_received` when the first offer lands, `under_approval` while any step is pending, `po_issued` on award |
| Pending receipt vs received vs confirmed | `pending_receipt` when the PO is dispatched and nothing has arrived; `received_by_warehouse` once a receipt exists; `confirmed_by_requester` only after the requester signs the delivery (a rejected receipt does not count) |
| Bilingual rail data | `GET /purchase-orders/{id}/lifecycle` returns per-stage `label_en`/`label_ar`, `state`, `reached_at`, `actor_id`, plus `next_action_en`/`next_action_ar` — the UI never invents copy |

## 2. Auditor document package (automatic, sealed)

`app/services/audit_package.py` assembles **GRN + awarded offer + PO + the full approval record +
invoice + match result + journal postings + notes**, hashes every component with SHA-256, and seals
the hash list into a manifest. `GET /purchase-orders/{id}/audit-package` re-verifies the manifest on
**every read** (`verification.intact`), so a document changed behind the API is detected.

* Assembled **automatically** inside `receiving.confirm_receipt` — the moment the evidence is complete
  — with an `audit_package.assembled` audit row marked `automatic: true`.
* A human reviewer with `audit_package:review` records `accept` or `query` with a comment.
* New evidence resets a previously **accepted** package to `assembled` and clears the reviewer: a seal
  describing stale documents cannot unlock a posting.
* Posting is refused until the package is `reviewed|accepted` (`audit_review_required`).

## 3. Three-way match as the gate to the books

The phase-1 match (PO ↔ confirmed receipt ↔ invoice, 8 issue codes, 0.5% per-line tolerance, partial
receipts) is now **stored** on the invoice and re-checked by the accounting service. An invoice
posting is refused when the match is not clean, when the invoice is not approved, or when the audit
package is not accepted — in that order, each with its own RFC 9457 code. A mismatch can still be
approved by a human with a written `override_reason`; it cannot be *posted* without a clean match.

## 4. The accounting module

| Piece | Detail |
| --- | --- |
| Double entry | `journal_entry` + `journal_line`, draft → posted → reversed. `validate_lines` refuses an empty or unbalanced entry and returns both totals (`unbalanced`) |
| Posting rules | Receipt accrual: **D 110100 inventory / C 110200 GR-IR** at PO cost. Invoice with an accrual: **D GR-IR at PO cost, D/C 530100 price variance, D 120100 VAT input, C 210100 AP** at invoice total. Invoice without an accrual: inventory is debited per line. Payment: **D 210100 AP / C 100100 bank** |
| Only the increment | A partial receipt accrues only the not-yet-accrued quantity; a second call with nothing new returns `duplicate_accrual` instead of a zero journal |
| Source linkage | Every entry carries `po_id`, `invoice_id`, `receipt_id`, `supplier_id` and a `match_snapshot`; `entry_view` exposes `created_by/at`, `posted_by/at` |
| Reversals | `reverse_entry` requires a written reason, mirrors every line, links both rows (`reversal_of` / `reversed_by`), and the trial balance sums posted **and** reversed rows so the pair nets to zero |
| Immutability | `journal_line`, `cost_allocation`, `p2p_stage_event` are append-only at the database level |
| Segregation of duties | A **manual** entry cannot be posted by its creator. Document-derived entries were already approved by the auditor and the invoice approver, so the controller may post their own |
| Supplier sub-ledger | `GET /accounting/supplier-account/{id}` → AP movements from posted entries, the payment ledger, and the live outstanding/overdue balance. Payment methods are suggestions; `execution: outside_platform` |
| Cost & analytical | Every inventory/expense debit needs a cost centre (`missing_cost_center`); posting writes a `cost_allocation` fact (cost centre, unit number, item, quantity, amount). `cost_report(group_by=cost_center\|item\|unit\|supplier\|po)` reads the facts, not the documents |
| Trial balance | `GET /accounting/trial-balance` over posted entries, `balanced` computed server-side |
| Master data | Chart of accounts (11 seeded codes) and cost centres (`CC-000` seeded), creatable and renamable per tenant; an account with posted lines cannot be deactivated (`account_in_use`) |

## 5. RBAC

Two new roles, seeded by migration `0015` and re-synced from `app/core/rbac.py` at bootstrap:

| Role | Can | Cannot |
| --- | --- | --- |
| `controller` | `accounting:read/write/post/reverse`, `cost:read/write`, `audit_package:read`, money + invoice + payment verbs | review the audit package, approve a purchase |
| `auditor` | read-only money, `audit_package:read/review`, `approval:read/decide` | post, draft, or approve a purchase |

`ledger:read` still gates `/supplier-balances`; the frontend mirror in `src/lib/permissions.ts` is
asserted against the backend map by `tests/test_frontend_permissions.py`.

## 6. Frontend

New pages and components, all bilingual (Arabic RTL default + English), permission-gated, wired to the
real API with real loading / empty / error states:

| Screen | Route | Permission |
| --- | --- | --- |
| `JournalPage` — list, entry drawer, post, reverse-with-reason | `/accounting` | `accounting:read` (post button only with `accounting:post`) |
| `AccountingReportsPage` — trial balance, cost analysis, supplier balances + **supplier ledger drawer** | `/accounting/reports` | `cost:read`, balances tab only with `ledger:read` |
| `AccountingSetupPage` — chart of accounts, cost centres | `/accounting/setup` | `accounting:read` |
| `P2PRail` — the eleven stages, bilingual, `aria-selected` on the current stage | PO detail | — |
| `AuditPackageCard` — seal status, component list, accept / query | PO detail | `audit_package:read` / `:review` |
| `PostingsCard` — entries, cost split, draft accrual / invoice posting with a cost-centre allocation | PO detail | `accounting:read`, actions need `accounting:write` |
| `SupplierAccountDrawer` — payable, overdue, journal movements, payment ledger | reports page | `ledger:read` |

`src/pages/accounting.test.tsx` (6 tests) proves the permission behaviour rather than the markup: no
`POST` at all when `accounting:post` is absent, the post call when present, reports never fetch
`/supplier-balances` or `/supplier-account` without `ledger:read`, and the rail renders eleven stages.

## 7. Evidence (real output, this branch)

```
$ cd backend && python -m pytest tests
583 passed in 38.56s

$ cd backend && python -m ruff check app tests
All checks passed!

$ python -m pytest tests/test_api_p2p_accounting_e2e.py tests/test_api_accounting.py \
      tests/test_services_p2p.py tests/test_services_accounting.py \
      tests/test_frontend_permissions.py -v
tests/test_api_p2p_accounting_e2e.py ...                                 [  4%]
tests/test_api_accounting.py ...............                             [ 29%]
tests/test_services_p2p.py .................                             [ 56%]
tests/test_services_accounting.py ........................               [ 95%]
tests/test_frontend_permissions.py ...                                   [100%]
====================== 62 passed in 9.81s ======================

$ cd backend && alembic upgrade head && alembic check      # fresh database
INFO  [alembic.runtime.migration] Running upgrade 0014_drop_theme_preference -> 0015_accounting, …
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
No new upgrade operations detected.

$ cd frontend && npx tsc --noEmit
(no output)

$ cd frontend && npx eslint src --max-warnings 0
(no output)

$ cd frontend && npx vitest run
 ✓ src/components/guard.test.tsx (3 tests) 45ms
 ✓ src/lib/format.test.ts (10 tests) 20ms
 ✓ src/i18n/i18n.test.ts (3 tests) 3ms
 Test Files  12 passed (12)
      Tests  66 passed (66)

$ cd frontend && npm run build
dist/assets/index-BLC7k_X2.js   474.88 kB │ gzip: 123.76 kB
dist/assets/charts-1jBeNFW4.js  407.41 kB │ gzip: 110.02 kB
✓ built in 6.37s
```

**What could not be run in this sandbox, and why it is still covered:** the sandbox has no PostgreSQL
that can run `alembic upgrade head` (no `pgcrypto`, no OpenSSL headers, no package manager), so the
PostgreSQL-only gates — the migration chain, `alembic check` against a real server, RLS isolation and
the new `REVOKE UPDATE, DELETE` proofs in `scripts/verify_rls.py` (now 13 phase-2 checks), and the
`postgres` CI job — run in GitHub Actions, not here. `alembic check` was still verified locally on a
fresh database to prove the migration matches the models with no drift. The frontend is verified by
typecheck, ESLint, jsdom tests and a production build; no browser binary exists in the sandbox, so
there is no Playwright run to paste.

## 8. Documentation

* `docs/DECISIONS.md` — 12 new entries (59–70) on derived stages, append-only books, the fixed chart of
  accounts, GR/IR behaviour with and without an accrual, the three posting gates, two-person control on
  manual entries only, reversal-instead-of-edit, analytical facts, sealing the package, automatic
  assembly, the controller/auditor split, and partial accrual.
* `docs/TRACEABILITY.md` — 17 new FR rows and 10 new AC rows, each pointing at the code and the test.
* `docs/PROGRESS.md` — phase-2 rows for every module, honest counts (583 backend / 66 frontend).
* `docs/API.md` — a new accounting section (posting rules, gates, error codes) and a regenerated
  endpoint inventory: **182 operations**.
