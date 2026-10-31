# Production-readiness plan (medium organisation, daily use)

Goal: a team of ~10–50 staff and a few thousand customers can run billing from this app every day without a developer in the room.

| # | Workstream | What "done" means | Status |
|---|---|---|---|
| 1 | Access and security | Login throttling per email+IP; password change for signed-in users; secure cookies/HSTS/headers when `DJANGO_DEBUG=0`; secrets only from env; no default key in production | done |
| 2 | Accountability | Append-only audit log of every state change, payment, discount, staff and settings edit with actor and time; admin-only viewer with filters | done |
| 3 | Company settings | Company name, address, tax ID, bank details, invoice footer and default payment terms editable by admin; used on invoices and emails | done |
| 4 | Collections | Overdue reminder emails (single, bulk, and `send_reminders` command), reminder counter and last-sent date, renewal-upcoming notices to customers | done |
| 5 | Day-to-day operability | Edit seats/lines on an active subscription (applies from next invoice, audited); CSV exports for subscriptions, payments, customers; global search; customer self-service profile | done |
| 6 | Operations | `backup` command (consistent SQLite snapshot + retention), `/healthz`, structured logging, gunicorn + whitenoise, Dockerfile, `.env.example`, optional PostgreSQL via `DATABASE_URL`, systemd timer examples | done |
| 7 | Verification | Tests for every new rule; 5k-subscription perf run; browser walkthrough of the full journey (signup → quote → active → invoice → payment → portal → reports) on desktop and mobile; security pass | done |
| 8 | Docs | Operator runbook: install, schedule, backup/restore, upgrade, roles, policies, known limits | done |

Explicit non-goals (documented limits): online card payments, proration of mid-period changes, credit notes/refunds, multi-currency, accounting-ledger revenue recognition.
