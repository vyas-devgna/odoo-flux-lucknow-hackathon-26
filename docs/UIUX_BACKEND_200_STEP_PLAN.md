# Selection-round refinement: execution plan

Local work only. The latest user instruction authorizes local Git checkpoints every 30 minutes during active work, authored as `vyas-devgna`. Pushes, deployments, history changes, paid services, and real charges remain unauthorized. This overrides the older handoff's publishing instructions.

## Execution and evidence

Keep the Django/template architecture and existing horizontal navigation. Preserve business records and the original Brightloop identity. Prioritize blocked tasks, financial errors, authorization, accessibility, then performance and visual detail. Each item is a concrete inspection, implementation, or verification task; an inspection may conclude that existing behavior is correct. A checklist is not proof of completion.

Owners: **V** visual worker; **I** interaction worker; **F** financial worker; **P** primary/integration. Workers use the requested `gpt-6-luna` / `xhigh` configuration. The primary owns integration and validation. No overlapping file ownership. Evidence and remaining items are recorded at the end of this document.

Dependencies: establish the baseline first; inspect before editing; run focused regressions after each fix; run the integrated suite after workers finish; capture and inspect the resulting desktop/mobile and light/dark UI. Use isolated databases for tests and scale data. Never reset the user's development database.

Legend: `[ ]` pending; `[x]` executed with evidence in the outcome ledger. An unresolved external integration remains pending even when its local code is tested.

## Frontend: 100 steps

### Foundation and visual system — V

- [x] UX001 Read the handoff and existing frontend token specification.
- [x] UX002 Capture the current desktop overview as a visual baseline.
- [x] UX003 Capture the current mobile overview as a visual baseline.
- [x] UX004 Inspect actual spacing against the four-pixel scale.
- [x] UX005 Inspect font sizes, weights, and line-height hierarchy.
- [x] UX006 Check money alignment and tabular numerals.
- [x] UX007 Check shared button, field, and toolbar heights.
- [x] UX008 Check panel borders, elevation, and radius consistency.
- [x] UX009 Check semantic status colors across both themes.
- [x] UX010 Fix shared visual defects at the component level.

### Navigation and shell — I

- [x] UX011 Inspect global and contextual navigation active states.
- [x] UX012 Verify mobile navigation opens and closes by keyboard.
- [x] UX013 Verify Escape dismisses menus and restores focus.
- [x] UX014 Check outside-click handling without lost button actions.
- [x] UX015 Verify global search opens using its keyboard shortcut.
- [x] UX016 Prevent stale search responses replacing newer results.
- [x] UX017 Render search results without interpreting customer HTML.
- [x] UX018 Preserve keyboard selection when results change.
- [x] UX019 Check profile, notifications, and theme controls after navigation.
- [x] UX020 Prevent duplicate global event listeners after HTMX swaps.

### Forms and guided operations — V

- [x] UX021 Inspect product and variant editing layout.
- [x] UX022 Inspect plan settings and advanced-option grouping.
- [x] UX023 Inspect customer creation labels and help text.
- [x] UX024 Inspect quotation creation and line-editor layout.
- [x] UX025 Inspect subscription amendment price comparison hierarchy.
- [x] UX026 Inspect partial-payment and balance-payment forms.
- [x] UX027 Check field error contrast and readable recovery guidance.
- [x] UX028 Check required, optional, readonly, and disabled presentation.
- [x] UX029 Check action placement and sticky actions on mobile.
- [x] UX030 Fix overflow or inconsistent form layout in shared patterns.

### Lists and operational density — V

- [x] UX031 Inspect subscription list hierarchy and action discoverability.
- [x] UX032 Inspect invoice list balances, dates, and statuses.
- [x] UX033 Inspect customer and catalog list density.
- [x] UX034 Check long customer/product names without clipped meaning.
- [x] UX035 Check sorting indicators and current filter visibility.
- [x] UX036 Check pagination labels and narrow-screen wrapping.
- [x] UX037 Inspect bulk selection and action-bar layout.
- [x] UX038 Keep horizontal scrolling scoped to genuinely wide tables.
- [x] UX039 Inspect empty-state explanation and next action.
- [x] UX040 Fix table and toolbar defects using shared CSS.

### Documents and overlays — I

- [x] UX041 Inspect invoice drawer open/close behavior.
- [x] UX042 Move focus into the document drawer when opened.
- [x] UX043 Trap focus within the active drawer.
- [x] UX044 Restore focus to the originating control on close.
- [x] UX045 Keep background content inert while a modal is open.
- [x] UX046 Provide an accessible document-loading state.
- [x] UX047 Provide document-load failure and retry handling.
- [x] UX048 Verify print and download use the correct document.
- [x] UX049 Add safe document-link copying with feedback if appropriate.
- [x] UX050 Verify overlay Escape and keyboard behavior after HTMX swaps.

### Portal and storefront — V

- [x] UX051 Inspect customer portal summary and upcoming-charge hierarchy.
- [x] UX052 Inspect portal subscription state and next-step guidance.
- [x] UX053 Inspect mobile invoice and payment history readability.
- [x] UX054 Inspect add-on and plan-change comparison on mobile.
- [x] UX055 Inspect cancellation and renewal explanations.
- [x] UX056 Inspect storefront plan comparison and billing-frequency clarity.
- [x] UX057 Inspect variant, quantity, and add-on controls.
- [x] UX058 Keep price summary and consent readable on narrow screens.
- [x] UX059 Inspect login/signup/reset forms in both themes.
- [x] UX060 Fix customer-facing visual inconsistencies without new branding.

### Interaction resilience and motion — I

- [x] UX061 Check dirty-form protection on boosted and ordinary navigation.
- [x] UX062 Prevent double form submissions without blocking validation.
- [x] UX063 Restore form controls after failed requests.
- [x] UX064 Preserve appropriate submitter behavior with confirmation dialogs.
- [x] UX065 Give asynchronous failures actionable feedback.
- [x] UX066 Check toast announcement, dismissal, and timeout behavior.
- [x] UX067 Check chart touch interaction without relying on hover.
- [x] UX068 Check chart teardown and initialization after navigation.
- [x] UX069 Respect reduced-motion preferences in JavaScript behavior.
- [x] UX070 Add repeatable interaction regressions for repaired behaviors.

### Responsive composition and accessibility — V

- [x] UX071 Check representative pages at 360/390px width.
- [x] UX072 Check intermediate tablet layout at 768px width.
- [x] UX073 Check compact desktop layout at 1280/1440px width.
- [x] UX074 Check wide desktop composition without stranded panels.
- [x] UX075 Check light-theme text, controls, and focus contrast.
- [x] UX076 Check dark-theme text, controls, and chart contrast.
- [x] UX077 Check touch target sizes and space between adjacent actions.
- [x] UX078 Check visible focus and skip-link presentation.
- [x] UX079 Check large text and long-content wrapping.
- [x] UX080 Capture and inspect final responsive screenshots.

### Frontend performance and state lifecycle — I

- [x] UX081 Inspect script loading and avoid unnecessary dependencies.
- [x] UX082 Check repeated navigation for leaked handlers or chart instances.
- [x] UX083 Abort or ignore obsolete asynchronous work.
- [x] UX084 Check overlays do not leave body scroll locked.
- [x] UX085 Keep loading states from causing avoidable layout shifts.
- [x] UX086 Handle clipboard failures without silent success.
- [x] UX087 Check modal and menu attributes match visible state.
- [x] UX088 Check keyboard shortcuts do not hijack text entry.
- [x] UX089 Run JavaScript syntax validation after edits.
- [x] UX090 Run browser checks with console/page-error collection.

### Integrated user-experience gate — P

- [x] UX091 Run the existing end-to-end acceptance journey.
- [x] UX092 Run the existing design-token audit.
- [x] UX093 Run accessibility checks on touched representative screens.
- [x] UX094 Independently inspect workers' desktop screenshots.
- [x] UX095 Independently inspect workers' mobile screenshots.
- [x] UX096 Verify staff/customer navigation remains role appropriate.
- [x] UX097 Verify financial and contract workflows remain discoverable.
- [ ] UX098 Verify CSS and JS changes preserve printable documents.
- [ ] UX099 Review the integrated diff for dead controls and accidental churn.
- [ ] UX100 Record measured results and exact remaining UX limitations.

## Backend: 100 steps

### Baseline and trust boundaries — P

- [x] BE001 Inspect repository status and preserve unrelated work.
- [x] BE002 Read requirements, architecture, and implementation notes.
- [x] BE003 Inventory domain services, callers, models, and tests.
- [x] BE004 Run the baseline backend suite and retain its log.
- [x] BE005 Identify development/runtime database configuration.
- [x] BE006 Inspect server-side role and ownership boundaries.
- [x] BE007 Inspect consequential view-to-service mutation paths.
- [x] BE008 Identify transaction gaps and stale-object validation.
- [x] BE009 Rank observed defects by financial/user impact.
- [x] BE010 Establish isolated test and performance databases.

### Authentication and input boundaries — P

- [x] BE011 Verify case-normalized unique customer email handling.
- [x] BE012 Verify password rules and expiring reset behavior via tests.
- [x] BE013 Verify suspended accounts cannot authenticate.
- [x] BE014 Verify staff capability checks and customer URL isolation.
- [x] BE015 Inspect CSRF and safe post-action redirects.
- [x] BE016 Inspect rate limiting at sensitive authentication endpoints.
- [x] BE017 Reject malformed storefront numeric inputs safely.
- [x] BE018 Bound storefront quantities before decimal/database calculations.
- [x] BE019 Reject unavailable or unrelated variants at the input boundary.
- [x] BE020 Add regression checks for repaired validation failures.

### Catalog and commercial snapshots — P

- [x] BE021 Verify explicit plan-base pricing avoids duplicate base fees.
- [x] BE022 Verify variant adjustments including the required 560 example.
- [x] BE023 Verify product/add-on availability and plan eligibility.
- [x] BE024 Align storefront tax selection with subscription service rules.
- [x] BE025 Verify quote/checkout previews match persisted line prices.
- [x] BE026 Preserve agreed live-contract base prices during unrelated changes.
- [x] BE027 Validate amendment quantity/effective-date inputs in the service.
- [x] BE028 Verify expired quotations cannot be accepted.
- [x] BE029 Verify accepted quote revisions and consent remain traceable.
- [x] BE030 Add focused commercial-pricing regressions.

### Lifecycle and checkout transactions — P

- [x] BE031 Reload and lock contracts before consequential transitions.
- [x] BE032 Prevent duplicate acceptance from stale requests.
- [x] BE033 Keep state changes and history records atomic.
- [x] BE034 Check pause/resume do not corrupt MRR history.
- [x] BE035 Prevent duplicate linked renewal terms.
- [x] BE036 Check cancellation timing and plan policies remain enforced.
- [x] BE037 Recheck amendment permissions after loading current state.
- [x] BE038 Keep checkout contract, lines, consent, and activation atomic.
- [x] BE039 Provide checkout retry protection using existing persistence patterns.
- [x] BE040 Verify failed checkout leaves no partial commercial records.

### Billing engine and calendar — F

- [x] BE041 Inspect all billing-run callers and eligible states.
- [x] BE042 Verify daily and weekly period calculations.
- [x] BE043 Verify monthly anchors and leap-year behavior.
- [x] BE044 Verify missed-period recovery remains bounded.
- [x] BE045 Verify ordinary-invoice period uniqueness constraints.
- [x] BE046 Verify repeated billing cannot advance a period twice.
- [x] BE047 Inspect PostgreSQL locking and SQLite-specific assumptions.
- [x] BE048 Verify discount usage and schedule advancement are atomic.
- [x] BE049 Verify expiry, pause, and cancellation boundaries.
- [x] BE050 Add regressions for any repaired billing defects.

### Financial documents and reconciliation — F

- [x] BE051 Verify finalized invoice snapshot immutability.
- [x] BE052 Verify cancellation and replacement links preserve history.
- [x] BE053 Verify credits cannot exceed eligible original value.
- [x] BE054 Verify partial payments reduce only the correct invoice balance.
- [x] BE055 Reject overpayments and invalid payment amounts.
- [x] BE056 Verify duplicate manual payment references are safe.
- [x] BE057 Verify pending/failed payments do not count as settled cash.
- [x] BE058 Verify sandbox payments remain distinct from real receipts.
- [x] BE059 Verify refund bounds, status, and test-money consistency.
- [x] BE060 Add regressions for repaired reconciliation defects.

### Provider, tax, and retry safety — F

- [x] BE061 Verify webhook signatures before side effects.
- [x] BE062 Keep webhook deduplication and processing transactionally consistent.
- [x] BE063 Verify failed event processing can be retried safely.
- [x] BE064 Validate provider amount, currency, and invoice association.
- [x] BE065 Avoid silently accepting mismatched or excess captures.
- [x] BE066 Verify provider pending outcomes do not trigger duplicate charges.
- [x] BE067 Verify discount limit/window/product-scope behavior.
- [x] BE068 Verify inclusive/exclusive tax rounding is deterministic.
- [x] BE069 Run existing payment/document/billing suites plus new regressions.
- [x] BE070 Report provider verification boundaries without claiming live execution.

### Automation and operations — P

- [x] BE071 Inspect outbox enqueue, delivery, retry, and failure behavior.
- [x] BE072 Prevent concurrent delivery of the same pending email.
- [x] BE073 Preserve retry state after SMTP failure.
- [x] BE074 Verify failed transactions cannot send stale emails.
- [x] BE075 Verify automation financial effects remain idempotent.
- [x] BE076 Inspect independent scheduler entry point and run bounds.
- [x] BE077 Run Django system and migration-consistency checks.
- [x] BE078 Check newly added logs do not expose secrets or tokens.
- [x] BE079 Document changed operational behavior and failure semantics.
- [x] BE080 Add focused outbox/operations regressions if changed.

### Reporting and measured performance — P

- [x] BE081 Verify recurring-revenue calculations use effective-dated events.
- [x] BE082 Verify daily/weekly/monthly/yearly normalization tests.
- [x] BE083 Verify cash, invoiced amounts, and outstanding are distinguished.
- [x] BE084 Inspect report filters and historical query boundaries.
- [x] BE085 Inspect hot pages for repeated per-row queries.
- [x] BE086 Measure a reproducible baseline on isolated scale data.
- [x] BE087 Apply only demonstrated query/performance improvements.
- [x] BE088 Remeasure changed paths with the same dataset and environment.
- [x] BE089 Report database, size, environment, and observed latencies.
- [x] BE090 Retain metric and permission regression coverage.

### Integration and delivery gate — P

- [x] BE091 Review each worker's patch and reported evidence.
- [ ] BE092 Run the complete integrated backend suite.
- [x] BE093 Run available concurrency tests and identify DB-specific gaps.
- [ ] BE094 Verify product-to-invoice-to-payment acceptance journey.
- [x] BE095 Verify rerun billing produces no duplicate ordinary invoice.
- [x] BE096 Verify another customer is denied access to protected records.
- [x] BE097 Verify startup and local application health after changes.
- [ ] BE098 Review final diff for accidental schema/data/dependency changes.
- [ ] BE099 Update concise working notes with verified fixes and known limits.
- [ ] BE100 Deliver exact evidence, remaining risks, and local changed-file paths.

## Outcome ledger

### First refinement checkpoint

The first three delegated passes used the requested `gpt-6-luna` / `xhigh` configuration with disjoint file ownership. Runtime token/effort usage metrics were not exposed. All code remains independently branded. No improvement multiplier or production-readiness claim is made.

- Baseline: 135 backend tests passed on SQLite.
- Contract regressions first reproduced seven failures (stale acceptance/activation, non-atomic history, unintended repricing, paused revenue, invalid amendment timing). Separate upgrade/downgrade regressions reproduced under-crediting and unsafe reversal of unissued increases. Those fixes pass focused tests.
- Checkpoint gate: 190 tests on SQLite (4 PostgreSQL-only skips) and PostgreSQL 16.2 (1 SQLite-backup-only skip), all applicable tests passed.
- Browser journey: 34/34 checks, including emailed identity verification using the isolated test outbox, ownership isolation, billing reruns, and server-restart persistence.
- Shared interactions: 43 browser assertions passed, including search result escaping/order, focus/inert handling, form retry, consent preservation, live-validation recovery, touch charts, theme restoration, 160-character headings, and 200% zoom. These combine live public pages with clearly identified temporary DOM fixtures for shell controls; they do not substitute for the authenticated journey.
- Visual capture: 64 screenshots at 1440/390px, plus 128 at 360/768/1280/2560px, both themes; no overflow/contrast/touch-target failures in the script's sampled routes. Manual review found and fixed daily-frequency wording, theme chart refresh, drawer toolbar wrapping, and long-heading overflow.
- Accessibility: 60 axe-core scans across representative staff/customer/public screens and both themes/viewports, zero violations after fixes. This is scoped automated evidence, not a whole-product accessibility certification.
- Scale: isolated 5,052-subscription / 17,872-invoice SQLite dataset; slowest of 26 server routes was 215 ms (3 runs, report cache cleared). Browser median LCP 424–592 ms and observed CLS 0 on four pages without throttling. Details: PERFORMANCE.md.
- System/migration checks, JavaScript syntax, Python compilation, and static collection passed. Deployment checks retain three contextual warnings: SAMEORIGIN framing for the built-in document viewer, plus HSTS subdomain/preload policy requiring the actual domain owner's decision.
- The token audit has three intentional categories: multiline textarea height, multi-select height, and computed automatic card alignment spacing. It is not reported as zero findings.

### Follow-up from supplied defect screenshots

Pending: compact and correctly labelled invoice PDFs; report print layouts; compact single-line invoice panels; unambiguous checkbox states; realistic demo data expanded from 52 to 78 subscriptions and 25 to 38 organizations. These are now the next delegated implementation passes. Preserve existing working records while expanding the demo.

### External and policy boundaries

Live SMTP delivery and the payment provider's real sandbox remain unverified without credentials. Immediate reductions on discounted periods, reversal of unissued adjustments, and ambiguous mixed-tax credits require the guided next-period/review path. INR only; a simplified tax model; per-process report-cache freshness and other previously documented limits remain. See IMPLEMENTATION_PLAN.md and OPERATIONS.md.
