# Frontend system

Server-rendered Django templates, HTMX (boosted navigation, live previews), Alpine (small local state) and Chart.js, all vendored under `static/vendor`; no build step.

## Grid and scale (enforced by `e2e/design_audit.py`)
- Spacing: 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 (`--s1…--s12`). Page gutter steps 16/24/32/40/48 px by viewport; card padding 24 (16 on mobile).
- Controls: 32 (toolbar), 40 (default), 48 (primary action) px (`--h-sm/md/lg`). Radii 6 · 8 · 12 · 16 · 24 · pill.
- Type: 12/16 · 13/20 · 14/20 · 16/24 · 20/28 · 28/36 · 40/44, Inter (self-hosted). Tabular figures for money.
- Motion: 120 / 200 / 320 ms, one ease-out curve and one spring for switches. A single page-settle on navigation; everything else answers an action. `prefers-reduced-motion` removes it all.
- Layout is fluid up to 2240 px: the dashboard uses a two-chart row and a right rail that scales with the viewport; forms get a contextual rail (help, recent changes) and a floating action bar; narrow screens collapse to one column with a slide-in menu and stacked tables.

## Components
Buttons (primary, accent, ghost, danger), chips, badges, tabs and contextual sub-navigation, menus, dialog (native `<dialog>`), command palette (Ctrl+K, live search), toasts, tooltips, skeleton, progress meters, summary strips, history lists with real stretched links, sortable tables, line editor, stepper, timeline, calendar heat-map.

## Documents
Invoices, credit notes, quotations/agreements and statements open in a right-hand slide-over (the app slides left) with Print and Download PDF. They are traditional A4 documents: letterhead, parties, itemised lines, tax summary, amount in words, terms, signature blocks, status watermark and an integrity code; the same markup prints cleanly from the browser (`@page` A4 with page numbers). Customers see only their own.

## Data visualisation
Series colours come from validated tokens `--c1…--c4` (light and dark sets pass the colour-blind separation, lightness and contrast checks of the dataviz palette validator). Lines are 2 px with an end dot; bars are ≤ 24 px with a 4 px data end and 2 px gaps in stacks; hairline solid grid; one tooltip lists every series with a crosshair; legends appear for two or more series and toggle series; every chart has a table twin.

## Accessibility
axe-core (WCAG 2.1 AA + best practice) reports zero violations on 36 staff, customer and storefront pages. Visible focus rings, skip link, labelled controls, keyboard-operable menus and dialogs (Escape closes), inert off-screen panels.
