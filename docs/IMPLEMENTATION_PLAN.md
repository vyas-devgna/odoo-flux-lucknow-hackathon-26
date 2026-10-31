# Implementation plan and progress

Legend: ✅ done · 🔧 in progress · ⬜ not started

| Milestone | Scope | Status |
|---|---|---|
| M0 Baseline | Accounts, catalogue, quotation→subscription lifecycle, idempotent billing, payments, portal, reports, audit | ✅ |
| M1 Design system | Tokens, self-hosted type, component set (button, badge, tabs, menu, dialog, sheet, toast, tooltip, skeleton, progress), responsive IA with contextual sub-navigation, motion | 🔧 |
| M2 Domain depth | Effective-dated contract changes + MRR movements, billing periods, credit notes, proration/upgrade/downgrade previews, auto-renew, scheduled cancel, cancellation reasons, quote acceptance consent | ⬜ |
| M3 Money | Payment statuses/reconciliation, webhook-safe provider integration (sandbox), PDF invoices, test-payment segregation | ⬜ |
| M4 Operations | Email outbox with retries, automation rules + tasks + notifications, staff permissions, rate limits | ⬜ |
| M5 Growth | Storefront and checkout, cohort/retention and movement reports with filters | ⬜ |
| M6 Proof | Seed (demo + 5k), concurrency tests, query-count tests, Playwright journey, performance log, README | ⬜ |

Decisions are recorded in `docs/ARCHITECTURE.md` (calendar, money, discount, lifecycle policies) and this file's change log below.

## Change log
