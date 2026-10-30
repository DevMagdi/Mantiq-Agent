# ProcureAI design system

The visual identity is Mantiq institutional: navy, gold, dense tables, quiet chrome. It is
implemented as CSS custom properties in `frontend/src/styles/tokens.css`, mapped into Tailwind in
`frontend/tailwind.config.ts`. Components consume tokens only — a hex colour inside a component is a
review defect.

**There is exactly one theme.** The product is always navy; there is no light theme and no theme
switch anywhere in the app (`session.tsx`, `Shell.tsx`, `types.ts` carry no `theme` field, and the
`User` payload no longer returns one). This is a deliberate brand and readability decision: every
screen, PDF and screenshot is the same institutional surface, and no component has to be reviewed
against two palettes. The colour families are navy, gold and the four semantic status colours — no
other hue may be introduced.

## 1. Colour

Hex values are the source of truth; each token also has an `-rgb` triple so Tailwind can apply
opacity modifiers (`bg-surface-raised/60`) without losing the token.

| Token | Hex | Use |
| --- | --- | --- |
| `--navy` / `--navy-800` | `#16283f` | brand base, logo plate |
| `--navy-950` | `#08121d` | deepest surface, rail base |
| `--navy-900` | `#0d1a2b` | login brand panel, text on gold |
| `--navy-700` | `#223b57` | hover on navy, chart neutral |
| `--gold` | `#c9a227` | primary action, active nav accent, focus ring |
| `--gold-600` | `#a8861d` | pressed gold, spine gradient end |
| `--gold-300` | `#e3c766` | gold text on navy, active icons |
| `--gold-200` | `#f2e3ae` | active nav label on its gold wash |
| `--gold-contrast` | `#0d1a2b` | text on a gold fill (buttons, selection) |
| `--surface` | `#0e1a28` | app background |
| `--surface-sunken` | `#0a1522` | inset wells, table heads |
| `--surface-alt` | `#132233` | secondary panels |
| `--surface-raised` | `#182a3e` | cards, drawers, popovers, inputs, hover rows |
| `--line` / `--line-strong` | `#24384d` / `#31465f` | borders, dividers / hover border |
| `--ink` | `#eef2f7` | primary text |
| `--ink-muted` | `#a9b7c7` | secondary text |
| `--ink-subtle` | `#8595a8` | timestamps, hints, labels |
| `--sidebar` / `--sidebar-raised` | `#08121d` / `#16283f` | navigation rail / rail hover + active wash |
| `--sidebar-line` | `#1b2c3f` | rail hairline |
| `--sidebar-ink` / `--sidebar-ink-muted` / `--sidebar-label` | `#e8eef6` / `#93a4b8` / `#70849b` | rail text / idle items / group labels |
| `--ok` / `--ok-ink` | `#1f7a4d` / `#4ec98c` | approved, verified, matched, savings |
| `--warn` / `--warn-ink` | `#b7791f` / `#e8b25a` | pending, partially received, low confidence |
| `--danger` / `--danger-ink` | `#b42318` / `#f2776b` | rejected, mismatch, overdue, breach |
| `--info` / `--info-ink` | `#1d5f9f` / `#6fb0e8` | informational chips, in transit |

There is no light-theme counterpart to any of these: the built CSS contains no `prefers-color-scheme`
block and no `[data-theme]` selector, so a light variant cannot be reached at runtime. The exact
values and the contrast matrix are asserted by `src/styles/tokens.test.ts`.

Status→tone mapping is centralised in `StatusChip` / `Badge` (`src/components/ui.tsx`), so a new
status cannot pick an arbitrary colour. The post-award statuses (`issued`, `in_transit`,
`partially_received`, `received`, `confirmed`, `closed`, `matched`, `mismatch`, `incomplete`,
`approved`, `partially_paid`, `paid`, `overdue`) are registered in the same map.

Charts use `var(--gold)`, `var(--gold-300)`, `var(--info)`, `var(--navy-700)`, `var(--ok)` and
`var(--warn)` in that order (`CHART_COLORS` in `DashboardPage.tsx`, `COLORS` in `AnalyticsPage.tsx`)
— series stay inside the palette instead of inventing hues.

## 2. Typography

| Role | Family | Size / weight |
| --- | --- | --- |
| Display | IBM Plex Sans Arabic (AR) / Inter (EN) | 28–32px, 600 |
| Page title | same | 22px, 600 |
| Section | same | 15px, 600 |
| Body | same | 14px, 400 |
| Table cell | same | 13px, 400 |
| Numeric | same with `font-variant-numeric: tabular-nums` | 13–14px |

Both families are self-hosted via `@fontsource` (no CDN, no FOUT on RTL reflow). Arabic gets
`line-height: 1.7`; Latin `1.5`. All numerals in tables, money and KPIs use `tabular-nums` so
columns align across digits and locales.

## 3. Space, radius, elevation

* Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 (`gap-1` … `gap-12`).
* Radii: `--radius-xs 4px`, `--radius-sm 6px`, `--radius 10px`, `--radius-lg 16px`.
* Elevation is three steps only: `--shadow-1` (controls), `--shadow-2` / `--shadow-card` (resting
  card), `--shadow-3` / `--shadow-pop` (drawer, popover). Cards also carry a 1px inset highlight so
  the top edge reads as light rather than as another border.
* Focus: `--focus-ring` rgba gold at 45%, always a 2px outline offset 2px — never removed.

## 4. Layout

* Shell: 268px rail (`--sidebar-width`) collapsing to 76px (`--sidebar-width-collapsed`), 56px
  topbar (`--header-height`), content max-width 1440px with 24px gutters at `lg`.
* The rail is collapsible and the choice persists in `localStorage` (`procureai.sidebar`). Collapsed
  it keeps 44px hit targets, shows icons only with a tooltip/`aria-label`, and separates groups with
  a hairline instead of a label.
* Data-dense pages use a 12-column grid; the comparison matrix scrolls horizontally inside its card
  while the supplier column stays pinned.
* Breakpoints follow Tailwind defaults; the sidebar collapses under 1024px, tables switch to
  card rows under 768px.

## 5. Signature components

| Component | File | Behaviour |
| --- | --- | --- |
| Login split-screen | `pages/LoginPage.tsx` | brand panel + form, MFA step, forced password change, bilingual |
| RFQ kanban board | `pages/RfqBoardPage.tsx` | dnd-kit columns per stage, deadline chips, drop = PATCH stage |
| Offer comparison matrix | `pages/RfqDetailPage.tsx::ComparisonMatrix` | TCO, landed cost, weighted score per offer, best-value highlight, per-line cells, low-confidence markers |
| Negotiation thread | `RfqDetailPage.tsx::NegotiationPanel` | round timeline, bilingual drafts, **policy guard bar** showing target/walkaway/decision codes |
| Approval inbox | `pages/ApprovalsPage.tsx` | role-filtered queue, chain steps, approve/reject/revision/split with comment |
| Savings dashboard | `pages/AnalyticsPage.tsx` | KPIs, savings by category/supplier, price trend, forecast with bounds |
| Agent activity feed | `pages/ActivityPage.tsx` | runs, steps, tool calls, policy decisions, latency and cost |
| HITL verification drawer | `RfqDetailPage.tsx::VerificationDrawer` | per-field value, confidence, provenance span, one-click verify, request-missing-info |
| Purchase order list | `pages/PurchaseOrdersPage.tsx` | status filter chips, open/awaiting KPIs, outstanding balance strip |
| PO lifecycle | `pages/PurchaseOrderDetailPage.tsx` | gold stepper, ordered vs received vs available per line, receipt timeline, invoice drawer with the three-way match table |
| Goods receipt drawer | `PurchaseOrderDetailPage.tsx::ReceiptDrawer` | per-line received/accepted/rejected quantities, warehouse, carrier, delivery note |
| Requester sign-off | `PurchaseOrderDetailPage.tsx::ConfirmDrawer` | quantity/quality/spec/document checks, mandatory comment on reject |
| Three-way match table | `PurchaseOrderDetailPage.tsx::InvoiceRow` | received vs invoiced vs available, price variance %, mismatch issue list |
| Payment suggestion | `PurchaseOrderDetailPage.tsx::PaymentDrawer` | method/terms/priority + reason list, reference mandatory, explicit “no money moves here” note |
| Supplier ledger | `PurchaseOrderDetailPage.tsx` sidebar card | invoiced / paid / outstanding / overdue + last ledger entries |
| Public quote form | `pages/PublicFormPage.tsx` | no-login, tokenised, AR/EN, structured line pricing |
| Supplier portal | `pages/PublicPortalPage.tsx` | masked contacts, category confirmation, pending-review notice |

Shared primitives live in `src/components/ui.tsx`: `Card`, `CardHeader`, `Kpi`, `Progress`,
`Badge`, `StatusChip`, `Avatar`, `Drawer`, `Tabs`/`TabList`/`TabPanel`, `Hint`, `Toast` +
`ToastProvider`/`useToast`, `Spinner`, `Skeleton`, `EmptyState`, `ErrorState`, `LoadingBlock` and
`useEscape`. Form controls and buttons use the `.btn-primary` / `.btn-ghost` / `.field` component
classes from `src/styles/index.css`, so a button is a real `<button>` with the token palette by
construction. Long tables use `DataTable` (TanStack Table: sorting, global filter, pinned numeric
columns, fixed row height so row rendering stays cheap); it is pagination-free by design and the
pages that need it paginate through the API.

## 6. RTL and bilingual rules

1. `<html lang="ar" dir="rtl">` by default; the language toggle flips `lang`/`dir` and persists.
2. Logical CSS properties only: `ms-*`, `me-*`, `ps-*`, `pe-*`, `text-start`, `text-end`. `left`/
   `right` appear only inside mirrored icons via `rtl:rotate-180` or `rtl:-scale-x-100`.
3. Numbers stay Latin in both languages (`ar-EG-u-nu-latn`): procurement teams read Western
   numerals, and mixing numeral systems in a table breaks scanning. There is no numerals toggle.
4. Dates and currency use `Intl` with the active locale; money always shows the ISO code
   (`1,234,500 EGP`).
5. Charts mirror axis order under RTL; tooltips stay anchored to the cursor.

## 7. Accessibility (WCAG 2.1 AA target)

* Contrast: body text ≥ 4.5:1, large text ≥ 3:1 against `--surface` / `--surface-alt` /
  `--surface-raised`. Measured ratios (surface / alt / raised): `--ink` 15.60 / 14.32 / 12.98,
  `--ink-muted` 8.59 / 7.89 / 7.14, `--ink-subtle` 5.73 / 5.26 / 4.77, `--gold` 7.25 / 6.66 / 6.03,
  `--gold-300` 10.55 / 9.68 / 8.77. Rail: `--sidebar-ink` 16.14, `--sidebar-ink-muted` 7.40,
  `--sidebar-label` 4.90 (4.66 at the top of the rail gradient), active `--gold-200` on the raised
  wash 11.62. These are enforced by `src/styles/tokens.test.ts`, which parses `tokens.css` and fails
  below 4.5:1 — a colour tweak cannot silently regress the palette.
* **Semantic colours are split into fill and ink.** The saturated `--ok/--warn/--danger/--info`
  values are 2.4–4.3:1 as text on the card surface, so they are only ever used as fills, borders and
  chart marks. Text uses `--ok-ink #4ec98c` (7.5:1), `--warn-ink #e8b25a` (8.2:1),
  `--danger-ink #f2776b` (5.7:1) and `--info-ink #6fb0e8` (6.8:1), exposed through the same Tailwind
  group (`text-ok-ink`, `text-danger-ink`, …). `TONE_CLASS` in `ui.tsx` is the only place that maps a
  tone to a colour, so this rule cannot be bypassed per page.
* Gold `#c9a227` is used as a *fill* with `--gold-contrast` text on top (7.2:1) or as `--gold-300`
  text (10.9:1 on the rail); raw `--gold` is never small text on navy.
* Every interactive element is a real `<button>`/`<a>`; clickable `<div>`/`<article>` is a lint error.
* Focus is always visible; drawers and dialogs trap focus (`Drawer` restores it on close) and
  `useEscape` closes them.
* Async results announce through `aria-live="polite"`; errors use `role="alert"`.
* Tables expose `<caption>`, scope attributes and a keyboard-reachable row action menu.
* All motion (`transition`, `animate-*`) is disabled under `prefers-reduced-motion: reduce`
  (`tokens.css` block at the end of the token file).
* Icon-only buttons carry `aria-label` in the active language.

## 8. Motion

150ms `ease-out` for hover/press, 220ms for drawers, 300ms for the kanban drop settle. No parallax,
no autoplay, no bounce. Skeletons shimmer once per second and stop under reduced motion.

## 9. Contribution rules

1. Add a token before adding a colour; add a translation key before adding a string.
2. Components never call the API directly — pages use TanStack Query hooks from `src/lib/api.ts`.
3. Every list view ships loading, empty and error states (`LoadingBlock`, `EmptyState`,
   `ErrorState`) in the same commit.
4. A new page registers its route in `src/App.tsx` and its label in both `ar.ts` and `en.ts`
   (`i18n.test.ts` fails otherwise).
