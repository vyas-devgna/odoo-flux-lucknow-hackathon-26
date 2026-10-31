# Handoff: take the UI/UX of Brightloop Studio from polished to flawless

Current local refinement work and fresh evidence are recorded in [UIUX_BACKEND_200_STEP_PLAN.md](UIUX_BACKEND_200_STEP_PLAN.md). The latest user instruction authorizes local checkpoints every 30 minutes as `vyas-devgna`, with no remote pushes; it supersedes the publishing instructions below. Earlier test and audit counts below are historical.

Audience: engineers continuing frontend and user-experience work. This earlier brief records interface priorities; it does not certify backend completeness. Read it alongside the current refinement plan and its evidence ledger.

## 1. State of the project (end of this session)

- Repo: `vyas-devgna/odoo-flux-lucknow-hackathon-26` (the name is fixed by the hackathon; never reference other subscription products in code, UI or docs). Branch `main`, last commit `4ca5fd3`, pushed.
- Stack: Django 5.2 monolith, server-rendered templates, HTMX (boosted navigation + live previews), Alpine (small local state), Chart.js. All JS/fonts are vendored in `static/vendor` and `static/fonts`. No build step, no npm.
- Quality gates that are green right now:
  - `manage.py test` → **135 tests** pass (SQLite and PostgreSQL).
  - `python e2e/journey.py` → **34/34** real-browser checks (26-step business journey, restart persistence, mobile/tablet overflow).
  - axe-core (WCAG 2.1 AA + best-practice) → **0 violations** on 36 pages.
  - `python e2e/design_audit.py` → only intentional findings (textarea 80 px, multi-select 96 px, `margin-top:auto`).
- Demo data: `manage.py seed_demo` (52 contracts, 240+ invoices). Logins in `README.md`. `seed_demo --reset` (DEBUG only) rebuilds.

## 2. Ground rules (non-negotiable)

1. **Commit identity:** author `vyas-devgna <141345367+vyas-devgna@users.noreply.github.com>`. No `Co-Authored-By`, no AI attribution in commits, PRs or files. Push to `origin main` fast-forward only; never force-push.
2. **Money is `Decimal`**, formatted with the `money` / `short_money` template filters. Never format currency in JS except charts/tooltips.
3. **Authorisation is server-side.** Hiding a button is not security. Every new view uses a decorator from `core/permissions.py`; portal querysets filter by owner.
4. **No competitor branding or comparison text anywhere** (templates, docs, comments, tests). `test_page_html_has_no_competitor_branding` guards the rendered HTML.
5. **Everything on the 4 px grid** (see §4). If you need a number that is not a token, add a token.
6. Do not weaken tests to make a change pass; fix the cause.

## 3. How to run, verify and look at it

```sh
cd app
.venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8765           # NOT 8000: another project may own it
.venv/bin/python manage.py test                                # ~11 s
```
Browser tooling (system Chromium at `/usr/bin/chromium`; Playwright lives in a throwaway venv, recreate if missing):
```sh
uv venv /tmp/pw && uv pip install --python /tmp/pw/bin/python playwright
CHROMIUM=/usr/bin/chromium /tmp/pw/bin/python e2e/journey.py          # full journey; screenshots in e2e/out/
/tmp/pw/bin/python e2e/design_audit.py http://localhost:8765 40       # off-scale spacing/size/type report
```
Also run axe-core: download `axe.min.js` (jsdelivr `axe-core@4.10.2`), inject it with Playwright on each page and call `axe.run({runOnly:['wcag2a','wcag2aa','wcag21aa','best-practice']})`. Treat any violation as a bug.

**Always look at your work.** Take screenshots at 1440×900, 1920×1080, 390×844 and in dark mode for every page you touch and read them. Do not claim visual verification from source.

### Environment traps (these cost real time)
- If the dev server is running and you change Python, it autoreloads; if you add a **model field** you must `makemigrations` **and `migrate` the dev DB**. A stale schema makes `core/context.py` return `company=None` and documents render with a blank letterhead.
- `pkill -f "runserver ..."` inside a Bash call kills the calling shell (exit 144). Find the PID with `ss -ltnp | rg :8765` and `kill` it.
- Playwright `networkidle` returns before an HTMX swap finishes; after clicking a boosted link/form use `e2e/journey.py`'s `cw()` helper (click, wait idle, +450 ms).
- Tool output may be compressed by the harness; write results to a file and read it when you need exact lines.
- Django's Message-ID uses `socket.getfqdn()`, which can stall seconds; `config/settings.py` pins `DNS_NAME`. Don't remove it.
- `X_FRAME_OPTIONS` is `SAMEORIGIN` on purpose (the document viewer iframes our own pages).
- `hx-boost` is on `<body>`. Anything that must be a normal download or new-window (PDF, CSV, external links) needs `hx-boost="false"`; anchors with `target` are not boosted. Navigation must go through real `<a href>` (see `.stretch` rows and the `go()` helper in `static/js/app.js`); `htmx.ajax(...)` does not update the URL.

## 4. Design system (source of truth: `static/css/app.css`, summarised in `docs/FRONTEND.md`)

- Spacing tokens `--s1…--s12` = 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48. Gutter steps 16/24/32/40/48 by viewport; card padding 24 (16 on mobile).
- Controls: `--h-sm` 32 (toolbars, filters, chips), `--h-md` 40 (default buttons/inputs), `--h-lg` 48 (primary actions). Radii 6 · 8 · 12 · 16 · 24 · pill.
- Type: 12/16 · 13/20 · 14/20 · 16/24 · 20/28 · 28/36 · 40/44, Inter self-hosted, tabular numerals for money (`.num`).
- Motion: 120/200/320 ms, `--ease`, `--ease-spring` for switches. One page-settle on navigation; everything else responds to an action; `prefers-reduced-motion` disables all of it.
- Colour: neutral canvas, white cards, black primary buttons, blue accent (`--accent` #2559e0 light / #4d7dff dark). Status pills use `--ok/--bad/--warn/--info/--vio` (AA on their tints). **Chart series** use `--c1…--c4` only; both light and dark sets were validated with the dataviz palette validator (colour-blind separation, lightness band, contrast). Re-run the validator if you change them.
- Do not use inline `style=` for spacing; use tokens/utility classes (`.mt-2`, `.mb-4`, `.row-flex`, …) or add a component class. About 120 inline styles remain in templates (all snapped to the grid) and should be migrated to classes over time.

### File map (frontend)
| Area | Files |
|---|---|
| Shell, navigation | `templates/base.html`, `core/nav.py` (top tabs + contextual sub-nav), `templates/store/base_store.html`, `templates/accounts/auth_base.html` |
| Components CSS | `static/css/app.css` (tokens → base → layout → components → pages → motion → responsive) |
| Behaviour | `static/js/app.js` (toasts, confirm dialog, menus, command palette, shortcuts, charts, document viewer, forms/dirty-state, bulk select) |
| Template helpers | `core/templatetags/ui.py` (`icon`, `badge`, `money`, `short_money`, `sorth`, `qs`, `in_words`, `avatar_color`, `notifications`, `flash_json`) |
| Lists / forms | `templates/crud/*`, `core/crud.py` (`Resource`, `StyledFormMixin`, money adornment, aria-labels), `templates/partials/{pager,strip,rail,stepper,sub_lines}.html` |
| Documents | `templates/documents/{base,invoice,quotation,statement}.html`, `billing/pdf.py` (ReportLab, Noto Sans for ₹) |
| Charts | `figure.chart` markup in report templates + JSON via `json_script`; builders in `core/views*.py`; renderer in `app.js` |
| Portal / store | `templates/portal/*`, `templates/store/*` |

## 5. What the user asked for and what is already done

- Top-nav layout, light mode default, dark mode, mobile slide-in menu, command palette (Ctrl+K), notifications bell, contextual sub-navigation.
- Fluid layout up to 2240 px (no empty side gutters), dashboard with two-chart row and scaling right rail, form pages with contextual rail and floating action bar, summary strips on list pages.
- Documents (invoice, credit note, quotation/agreement, statement) open in a **right-hand slide-over** with Print / PDF; the app slides left. Traditional A4 layout with letterhead, tax summary, amount in words, terms, signatures, watermark, integrity code, print page numbers.
- Accessibility pass (zero axe violations), keyboard support, reduced motion.

## 6. Backlog for flawless UX (prioritised; verify each with screenshots)

### P0: do first
1. **Dark-mode audit of every page.** Only dashboard, palette, dialogs and a few lists were inspected. Check documents (they are white paper inside a dark app: ensure the viewer chrome looks right), charts (legend, tooltip, table view), calendar heat-map, hbar, steppers, pills, auth pages, store.
2. **Ultra-wide and small-laptop review (1280, 1536, 1920, 2560)** of every secondary page: customers, settings, automation, outbox, audit, team, catalogue lists, portal, store. Look for stranded narrow cards, unbalanced columns, tables with huge empty middles. Prefer converting stranded `max-width` forms into `form-layout` + rail (the pattern exists).
3. **Mobile pass (390 and 360):** every page, every dialog and the document viewer. Check tap targets ≥ 40 px, bulk-action bar, sticky `.form-actions` over the keyboard, line editor stacked rows, charts' touch tooltips (currently hover/mouse driven; add `pointerdown` handling and a larger hit area).
4. **Document viewer polish:** focus trap inside the drawer, restore scroll position, keyboard `P` to print, loading skeleton, error state if the frame fails, preserve open document on boosted navigation (currently closes). Add "Email this document" (reuse `send_invoice` / `email_quotation`) and "Copy link" to the toolbar. On mobile consider a full-screen sheet with a bottom toolbar.

### P1: interaction quality
5. **Live validation** for forms: inline errors on blur (dates, money, min quantity vs plan), disable submit while invalid, preserve scroll to first error after a failed POST.
6. **Optimistic/skeleton states:** replace the opacity dim on `.htmx-request` with skeleton blocks for the live cost preview and storefront summary; add progress feedback to long actions (billing run, CSV import).
7. **Tables:** sticky header inside long lists (needs a scroll container strategy because `.tw` is `overflow-x:auto`), column visibility/density toggle, saved filters, keyboard row navigation (↑/↓/Enter) with roving tabindex, shift-click range selection for bulk actions.
8. **Command palette:** recent items, contextual actions ("Record payment for INV…"), typed commands (`inv 123`, `cust acme`), highlight matches.
9. **Undo patterns** instead of confirm dialogs where reversible (toast with Undo for archive/disable, mark task done).
10. **Date inputs** use the browser's locale (`mm/dd/yyyy`); build a small accessible date picker or set explicit `en-IN` formatting hints; add relative shortcuts (Today, Start of month) in filter bars.
11. **Notification centre:** the bell lists counts; add read/unread persistence (`Notification.read_by` exists), grouping and deep links.

### P2: visual refinement
12. Migrate remaining inline `style=` to classes; add `.stack-sm/.stack-md` helpers; delete dead CSS (`.testmode`, `.compare` variants) after grepping.
13. Illustration/empty-state system: consistent icon + headline + one clear action; first-run onboarding checklist for admin (exists on empty dashboard only).
14. Storefront design: hero, plan comparison table, FAQ, trust copy, plan-selection motion; checkout success screen with next steps.
15. Customer portal home: timeline of upcoming charges, payment-method preferences surfaced, clearer "what happens next" copy per state.
16. Chart refinements: direct labels on last points, annotations for price changes/renewals, brush/zoom on 12-month charts, consistent number formatting between axis, tooltip and table.
17. Microcopy audit: replace jargon (MRR, ARR, ageing) with plain language plus a tooltip; consistent verbs on buttons; error messages that say what to do next.
18. Print styles for app pages (reports, lists) in addition to the documents.
19. Localisation scaffolding (`{% trans %}`), Indian number grouping already in `money`; add date/format settings and right-to-left readiness only if requested.

### P3: engineering hygiene
20. Add Playwright visual-regression snapshots for ~20 key screens (light/dark × desktop/mobile) to `e2e/` with tolerances, runnable in one command.
21. Extend `e2e/design_audit.py` to fail CI on off-scale values and to check tap-target size and colour contrast per theme.
22. Split `static/js/app.js` into modules (palette, charts, viewer, forms) without introducing a bundler (native ES modules with `defer`).
23. Performance budget for the frontend: Lighthouse on dashboard and list pages (target LCP < 1.5 s on the dev server, CLS 0). `scrollbar-gutter: stable` is already set to avoid layout shift.

## 7. Definition of done for any UI change

- Screenshots reviewed at 1440, 1920, 390, light and dark; no clipped text, no side voids, no horizontal scroll.
- axe-core: zero violations on the touched pages. Keyboard: every control reachable and operable, focus visible, Esc closes overlays.
- `e2e/design_audit.py`: no new off-scale findings.
- Reduced-motion respected; no animation that delays an action by more than 200 ms.
- `manage.py test` and `python e2e/journey.py` still green; add or update tests when behaviour changes (document content, permissions, redirects).
- Commit with a descriptive message as `vyas-devgna`; push fast-forward to `origin main`.

## 8. Known limitations to be honest about in any report

- Razorpay hosted checkout is implemented and unit-tested with signed payloads but has not been exercised against the provider's live sandbox (no credentials). Real SMTP delivery is unverified here.
- Plan changes keep the billing frequency; there is no customer-segment price list; tax is a simple per-line model, not statutory compliance.
- The reports cache is per process (60-120 s).
- Not every secondary screen has been reviewed at every breakpoint and theme; §6 P0 exists to close that gap.
