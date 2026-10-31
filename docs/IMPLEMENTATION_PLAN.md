# Implementation plan and status

## Current local refinement

The maintained [100-step frontend and 100-step backend execution plan](UIUX_BACKEND_200_STEP_PLAN.md) records the current audit, fixes, independent test evidence, and outstanding work. The milestone table below describes earlier delivery stages, not a certification of completeness.

This pass adds atomic/current-state contract transitions, durable checkout retry receipts, email verification before attaching existing billing records, transactional webhook processing, stricter financial document/payment operations, full credit allocation for eligible reversals, concurrent outbox delivery claims, role-aware search/commands, resilient dialogs/forms/charts, and responsive/theme/accessibility corrections. The supplied print, checkbox, panel-sizing, and richer demo-data screenshots are the next work items.

Only local checkpoints as `vyas-devgna` are authorized, every 30 minutes during active work. No pushes or deployment.

| Milestone | Scope | Status |
|---|---|---|
| M0 Baseline | Accounts, catalogue, quotation → subscription lifecycle, idempotent billing, payments, portal, reports, audit | done |
| M1 Design system | Tokens, self-hosted type, components, contextual navigation, command palette, motion, dark mode, responsive layouts | done |
| M2 Domain depth | Effective-dated contract changes, billing periods, credit notes, proration and previews, auto-renew, scheduled cancel, quote consent | done |
| M3 Money | Payment statuses and reconciliation, verified webhooks, PDF invoices, sandbox segregation | done |
| M4 Operations | Email outbox, automation rules, tasks and notifications, staff capabilities, rate limits | done |
| M5 Growth | Storefront and checkout, cohort/MRR/aging/breakdown reports with filters | done |
| M6 Proof | Seed + bulk generator, 125 backend tests (SQLite and PostgreSQL), concurrency test, query budgets, Playwright journey, performance log | done |

## Known limitations (deliberate)

- **Online payment provider:** Razorpay hosted checkout is implemented and unit-tested with signed payloads, but has not been run against Razorpay's live sandbox from this repository (no credentials). Without keys it is shown as not configured.
- **Email delivery:** verified with the console and test backends; real SMTP delivery needs your server credentials.
- **Plan changes** keep the billing frequency (daily/weekly/monthly/yearly); switching frequency means a new contract.
- **Tax** is a simple per-line rate model (exclusive or inclusive, effective dates); it is not statutory GST/VAT compliance (no place-of-supply rules, no HSN).
- **Price lists** per customer segment and product-level quantity tiers are not implemented; quantity tiers exist per plan.
- **Stored payment methods:** customers can save a payment *preference* (UPI id, bank nickname); card tokenisation is left to the provider and no automatic charging exists.
- **Multi-currency** is not supported (INR display).
- **Revenue recognition:** MRR/ARR and cash are operational metrics, not accounting-ledger revenue.
- **Reports cache** is per-process (60–120 s TTL); with several workers another worker may show figures up to that long after a change.
