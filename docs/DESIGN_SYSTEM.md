# ProcureAI design system

The visual identity is Mantiq institutional: navy, gold, dense tables, quiet chrome. It is
implemented as CSS custom properties in `frontend/src/styles/tokens.css`, mapped into Tailwind in
`frontend/tailwind.config.ts`. Components consume tokens only — a hex colour inside a component is a
review defect.

## 1. Colour

Hex values are the source of truth; each token also has an `-rgb` triple so Tailwind can apply
opacity modifiers (`bg-surface-raised/60`) while the theme swaps underneath.

| Token | Dark (default) | Light | Use |
| --- | --- | --- | --- |
| `--navy` / `--navy-800` | `#16283f` | `#16283f` | brand base, sidebars |
| `--navy-900` | `#0d1a2b` | — | deepest surface, logo plate |
| `--navy-700` | `#223b57` | — | hover on navy |
| `--gold` | `#c9a227` | `#c9a227` | primary action, active nav, focus ring |
| `--gold-600` | `#a8861d` | `#a8861d` | pressed gold, gold text on light |
| `--gold-300` | `#e3c766` | `#e3c766` | gold accents on dark |
| `--surface` | `#0f1b2a` | `#f6f7f9` | app background |
| `--surface-alt` | `#142436` | `#ffffff` | cards |
| `--surface-raised` | `#1a2c41` | `#ffffff` | drawers, popovers |
| `--line` | `#27384d` | `#dde3ea` | borders, dividers |
| `--ink` | `#eef2f7` | `#16283f` | primary text |
| `--ink-muted` | `#a9b7c7` | `#4c5c70` | secondary text |
| `--ink-subtle` | `#7a8a9d` | `#6b7b8f` | timestamps, hints |
| `--ok` | `#1f7a4d` | same | approved, verified, savings |
| `--warn` | `#b7791f` | same | pending, low confidence, SLA |
| `--danger` | `#b42318` | same | rejected, blocked, breach |
| `--info` | `#1d5f9f` | same | informational chips |

Status→tone mapping is centralised in `StatusChip` / `Badge` (`src/components/ui.tsx`), so a new
status cannot pick an arbitrary colour.

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
* Shadows: `--shadow-card` (resting card), `--shadow-pop` (drawer, popover, dropdown).
* Focus: `--focus-ring` rgba gold at 45%, always a 2px outline offset 2px — never removed.

## 4. Layout

* Shell: 264px sidebar (72px collapsed), 64px topbar, content max-width 1600px with 24px gutters.
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
3. Numbers stay Latin by default; the numerals toggle renders Arabic-Indic digits in *display*
   contexts only (never in inputs or money fields).
4. Dates and currency use `Intl` with the active locale; money always shows the ISO code
   (`1,234,500 EGP`).
5. Charts mirror axis order under RTL; tooltips stay anchored to the cursor.

## 7. Accessibility (WCAG 2.1 AA target)

* Contrast: body text ≥ 4.5:1, large text ≥ 3:1 against `--surface`/`--surface-alt` in both themes
  (verified for navy-on-gold and gold-on-navy pairs).
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
