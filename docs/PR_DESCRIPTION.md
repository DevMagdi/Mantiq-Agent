# Navy-only institutional redesign + post-PO lifecycle (receipts → 3-way match → settlement)

Two bodies of work on one branch:

1. **Visual overhaul** — the app now ships **one** theme (Mantiq navy + gold), the light theme and
   every trace of the theme toggle are gone, and the shell/navigation were rebuilt to a denser,
   more institutional standard.
2. **Post-PO lifecycle** — goods receipts with warehouse + requester sign-off, a deterministic
   three-way match, human invoice approval, payment *suggestion*, and a supplier ledger with
   outstanding/overdue balances. Built API → services → models → adapters → tests, with the agent
   kept out of every one of those mutations.

## What changed

### Design — one theme, no toggle

| Before | After |
| --- | --- |
| `[data-theme]` light/dark blocks in `tokens.css`, `darkMode` in Tailwind, `theme`/`setTheme` in `session.tsx`, a Sun/Moon button in the shell, `common.theme`/`common.dark`/`common.light` strings, `User.theme` | Single navy token set; `theme`/`numerals` removed from the API payload and dropped from the database (`0014_drop_theme_preference`); `index.html` no longer sets `data-theme` |

* **Tokens** (`src/styles/tokens.css`) — explicit surface ladder (`--surface-sunken` → `--surface` →
  `--surface-alt` → `--surface-raised`), a dedicated rail palette (`--sidebar*`), three shadows
  (`--shadow-1/2/3`) with an inset top highlight so cards read as light rather than as borders,
  radius/motion/`--focus-ring` tokens, thin scrollbars, gold selection, and the `prefers-reduced-motion`
  kill-switch the constraints require.
* **Contrast is now measured, not assumed.** The saturated semantic colours are 2.4–4.3:1 as text on
  the card surface, so they are fill/border/chart colours only; text uses new `--ok-ink`,
  `--warn-ink`, `--danger-ink`, `--info-ink` (5.7–8.2:1). Body text is 15.4:1, muted 7.7:1, subtle
  4.46:1, gold-on-navy 10.9:1, gold fill with `--gold-contrast` 7.2:1. All measured pairs are listed
  in `docs/DESIGN_SYSTEM.md` §7. No colour family outside navy + gold + ok/warn/danger/info.
* **Sidebar** (`components/Sidebar.tsx`, `lib/nav.ts`) — grouped navigation (Overview / Sourcing /
  Fulfilment / Insight), gold active rail drawn with a logical `start-0` pseudo-element so RTL
  mirrors for free, collapse to a 72px icon rail persisted in `localStorage`, 40px collapsed targets,
  `aria-label` on every icon-only link, tenant plate in the footer, hairline separators instead of
  labels when collapsed, mobile drawer with backdrop.
* **Shell** — 56px translucent top bar, live/polling indicator from the WebSocket hub, notification
  popover with mark-read/mark-all-read wired to the real endpoints, locale toggle, avatar/role block,
  content max-width container. `PageHeading` gained an eyebrow line and consistent spacing rhythm.
* Every old page inherits the new `.card`, `.btn-*`, `.input`, `.chip`, `.table-dense` component
  classes, so the redesign is not limited to the new screens.

### Post-PO lifecycle

**Models** (`app/db/models/entities.py`): `GoodsReceipt`, `SupplierInvoice`, `SupplierLedgerEntry`,
`PurchaseOrder.closed_at`.

**Services**
* `app/services/receiving.py` — `create_receipt` (per-line received/accepted/rejected, over-receipt
  and negative-quantity rejection), `confirm_receipt` (requester checks quantity/quality/spec,
  reject requires a comment), `recompute_po_status`, `close_po`, `timeline`, `outstanding_lines`.
* `app/services/invoicing.py` — `create_invoice`, `compute_match` (the three-way match),
  `approve_invoice`, `record_payment`, `suggest_payment`, `supplier_balance`, `supplier_balances`.

**API** (`app/api/v1/orders.py`, tag `orders`) — PO list/detail with lifecycle, receipts, receipt
confirmation, invoices, re-match, approval, payment recording, supplier balances, and the PO
PDF/Excel/ERP hand-off moved out of the RFQ router.

**RBAC** — new permissions `po:read`, `po:update`, `receipt:create`, `receipt:confirm`,
`invoice:create`, `invoice:approve`, `payment:record`, `ledger:read`, and a `warehouse`
(Warehouse / Stores) role that has `receipt:create` **and not** `receipt:confirm`. Separation of
duties is a permission, not a convention; a 403 carries `required_permission`.

**Frontend** — `pages/PurchaseOrdersPage.tsx` (status filter, open/awaiting-receipt/awaiting-requester
KPIs, outstanding strip, supplier balance table) and `pages/PurchaseOrderDetailPage.tsx` (gold
lifecycle stepper, ordered vs received vs available per line, receipt timeline, invoice drawer,
three-way match table with variance %, mismatch issue list, approve drawer with mandatory override
reason, payment drawer with the deterministic suggestion and its reasons, supplier ledger card).

### The safety model still holds: LLM proposes, policy + human decide

* `STAGES` still ends at `approval`. Receiving, matching, approving and paying are **not** agent
  stages and have **no** tools.
* The three-way match is pure arithmetic: invoiced quantity is capped by the *confirmed* receipt
  (never by the raw warehouse entry), previously invoiced quantities are subtracted so partial
  invoicing cannot double-bill, and the price is compared with the PO line inside a per-line 0.5%
  tolerance.
* `approve_invoice` refuses a non-matching invoice unless a written `override_reason` is supplied;
  the text lands in the invoice notes and the audit row records `overridden: true`.
* `suggest_payment` is rule-based (currency vs tenant currency, supplier country, value, preferred
  status, due date) and every result carries `requires_human_approval: true` and
  `execution: "outside_platform"`. `record_payment` books a transfer that already happened (mandatory
  external reference, no overpayment) — nothing in the codebase can move money.
* New regression test: `tests/test_agents_graph.py::test_post_award_human_actions_have_no_agent_tool`
  fails the build if `record_payment`, `approve_invoice`, `pay_invoice`, `execute_payment`,
  `create_invoice`, `confirm_receipt`, `accept_delivery` or `close_po` is ever registered as a tool.

## Verification (real output)

```text
$ cd backend && python -m pytest tests --cov=app
513 passed in 38.75s
TOTAL                                8916   1335    85%
  app/api/v1/orders.py                  234      6    97%
  app/services/invoicing.py             242     14    94%
  app/services/receiving.py             163     10    94%

$ python -m alembic upgrade head && python -m alembic check
Running upgrade 0013_post_po_lifecycle -> 0014_drop_theme_preference
No new upgrade operations detected.

$ ruff check app tests
All checks passed!

$ cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
tsc --noEmit                        OK
eslint . --max-warnings 0           OK
Tests  27 passed (27)
✓ built in 5.61s   (index 393.67 kB / 106.47 kB gzip, charts chunk 407.41 kB / 110.02 kB gzip)
```

New tests: `tests/test_services_receiving.py` (17), `tests/test_services_invoicing.py` (20),
`tests/test_api_post_po.py` (12, full HTTP cycle receipt → confirm → invoice → approve → pay → close),
plus the two post-award frontend tests in `frontend/src/pages/pages.test.tsx`.

Live check against the running stack (SQLite + uvicorn + vite), demo tenant `nile`:

```text
GET /api/v1/purchase-orders            PO-2026-00001  10,141,000.00 EGP  status=confirmed
GET /api/v1/purchase-orders/{id}       receipts: GR-2026-00001 (confirmed), GR-2026-00002 (confirmed)
                                       invoice: INV-00001 partially_paid 6,121,000 / paid 2,448,400
                                       match: matched, 2/2 lines ok, tolerance 0.5%
GET /api/v1/supplier-balances          Suez Metal Works outstanding 3,672,600.00 EGP, overdue 0, 1 open invoice
```

## Documentation

`docs/DESIGN_SYSTEM.md` (single theme, palette, measured contrast, rail behaviour, new screens),
`docs/AGENT_DESIGN.md` (forbidden post-award verbs + “the post-award loop has no agent”),
`docs/DECISIONS.md` (#31–#40), `docs/API.md` (post-award walkthrough + regenerated 158-operation
inventory), `docs/PROGRESS.md`, `docs/TRACEABILITY.md` (FR-PO-2…6, AC-PO-2…4, NFR-UX-1).

## Honest limits

* No browser E2E run: there is no Playwright/browser in this environment. The UI is verified by the
  production build, `tsc`, ESLint and 27 component/interaction tests; the API was exercised live with
  curl against the seeded demo tenant.
* No real payment execution, by design. The bank/ERP integration that performs a transfer is out of
  scope; the platform records the transfer that finance already made.
* SQLite only in this sandbox — Postgres-specific paths (RLS, pgvector) remain guarded and untested
  here, as before.
* The analytics page is still not lazily imported; the post-award screens ship in the main chunk.
