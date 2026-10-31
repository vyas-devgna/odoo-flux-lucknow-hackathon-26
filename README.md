# Brightloop Studio

Subscription business platform: products and plans, quotations, contracts, recurring billing, payments, a customer portal, a storefront, automation and revenue analytics, in one Django application.

Lucknow Hackathon ’26 · Virtual round · Team leader: **Vyas Devgna** ([@vyas-devgna](https://github.com/vyas-devgna))

![flow](docs/flow.svg)

| Area | What works |
|---|---|
| Sales | Customers (CSV import, statements), products with priced variants and add-ons, per-product tax, plans (daily/weekly/monthly/yearly, quantity tiers, contract rules), quotation templates, quotations with revisions, alternatives, upsells, email, print and online acceptance with a consent record |
| Contracts | Draft → Quotation → Confirmed → Active → Closed (+ Paused, Cancelled), effective-dated change history, plan/seat/add-on amendments with live cost preview and proration, scheduled amendments, auto/manual renewal, scheduled cancellation, minimum terms, cancellation reasons |
| Billing | Idempotent engine with explicit billing periods, locked invoice snapshots, discounts (fixed/percent, product/plan/subscription scope, limits, windows), exclusive/inclusive tax, credit notes, adjustment and replacement invoices, PDF/print/email, bulk actions, dunning, retry of failed subscriptions |
| Money | Payments with statuses (received / awaiting confirmation / failed), customer-reported payments reconciled by staff, refunds, idempotency keys, optional Razorpay hosted checkout with verified, deduplicated webhooks; sandbox money never counts as revenue |
| Customers | Portal with subscriptions, invoices, PDF, add-on and plan changes, pause/cancel/renew where the plan allows, billing details, payment preferences; public storefront with checkout |
| Operations | Role + capability permissions, audit log, email outbox with retries, rule-based automation, notifications and tasks, rate limiting, backups, health check |
| Insight | Overview, revenue, recurring-revenue bridge (new / expansion / contraction / churn), retention cohorts, receivables aging, breakdown by plan/product/customer/salesperson, CSV and print |

## Run it

```sh
uv venv .venv && uv pip install -r requirements.txt       # Python 3.13; or: python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo                      # 52 contracts, 240+ invoices, payments, credits, refunds, history
.venv/bin/python manage.py runserver 127.0.0.1:8765
```

Open <http://127.0.0.1:8765>. Demo accounts (development data only):

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@brightloop.test` | `Admin@12345` |
| Staff: sales + billing | `staff@brightloop.test` | `Staff@12345` |
| Staff: billing + finance | `finance@brightloop.test` | `Finance@12345` |
| Staff: sales + catalogue | `catalog@brightloop.test` | `Catalog@12345` |
| Customer (portal) | `maya@acme.test` | `Customer@123` |

`seed_demo --reset` wipes and rebuilds (development only). `seed_demo --bulk 5000` adds thousands of directly inserted subscriptions for performance testing.

## Scheduler (required for recurring billing)

The web server never bills by itself. One scheduler entry point does billing, automation rules and email delivery, and is safe to run every few minutes (file lock, idempotent, catches up after downtime):

```
*/10 * * * *  cd /app && .venv/bin/python manage.py run_scheduled
30 2 * * *    cd /app && .venv/bin/python manage.py backup --keep 14
```

Or keep one process alive: `manage.py run_scheduled --every 300`. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for systemd units, restore, PostgreSQL and security notes.

## Configuration (environment variables, see `.env.example`)

- **Email:** set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`. Without `EMAIL_HOST` mail prints to the console and is **not delivered**. Every message goes through the outbox (Settings → Email outbox) so failures are visible and retried.
- **Database:** SQLite by default. For PostgreSQL: `uv pip install -r requirements-postgres.txt` and set `DATABASE_URL=postgres://user:pass@host:5432/db`.
- **Online payments:** set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`; add the webhook URL shown in Settings → Payments. Without keys the integration is shown as not configured and manual payment recording stays fully operational. No card data is ever stored.
- **Production:** `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`; run behind TLS. `Dockerfile` included.

## Verify it

```sh
.venv/bin/python manage.py test                           # 123 backend, security, billing, payments and report tests
DATABASE_URL=postgres://... .venv/bin/python manage.py test   # same suite on PostgreSQL (includes parallel-billing test)
python e2e/journey.py                                     # real-browser 26-step acceptance journey + restart persistence + mobile/tablet overflow
.venv/bin/python manage.py perf_check --cold              # page latency at the current dataset size
```

`e2e/journey.py` needs `playwright` (`uv pip install playwright`) and a Chromium (`CHROMIUM=/path`). It writes screenshots to `e2e/out/`.

## Documentation

- [Requirements and acceptance checks](docs/REQUIREMENTS.md) · [Architecture and policies](docs/ARCHITECTURE.md) · [Metric definitions](docs/METRICS.md)
- [Operations runbook](docs/OPERATIONS.md) · [Performance log](docs/PERFORMANCE.md) · [Implementation plan and known limits](docs/IMPLEMENTATION_PLAN.md)
