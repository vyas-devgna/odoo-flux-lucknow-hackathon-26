<div align="center">

# Brightloop Studio

### A clearer rhythm for recurring business.

From the first quotation to the next renewal.<br />
One connected workspace for your team. One simple portal for your customers.

**Django 5.2 · PostgreSQL / SQLite · Server-rendered UI · INR**

[Get started](#get-started) · [Explore the workflows](#the-workspace) · [Run the checks](#verify-it) · [Operations guide](docs/OPERATIONS.md)

Lucknow Hackathon ’26 · Built by **[Vyas Devgna](https://github.com/vyas-devgna)**

</div>

---

![The Brightloop commercial lifecycle: quotation, subscription, billing, payment and renewal](docs/flow.svg)

## The workspace

Brightloop brings the commercial lifecycle into one modular Django application. Horizontal navigation, contextual actions, light and dark themes, and a focused mobile portal keep everyday work close at hand.

| Area | What works |
|---|---|
| Sales | Customers (CSV import, statements), products with priced variants and add-ons, per-product tax, plans (daily/weekly/monthly/yearly, quantity tiers, contract rules), quotation templates, quotations with revisions, alternatives, upsells, email, print and online acceptance with a consent record |
| Contracts | Draft → Quotation → Confirmed → Active → Closed (+ Paused, Cancelled), effective-dated change history, plan/seat/add-on amendments with live cost preview and proration, scheduled amendments, auto/manual renewal, scheduled cancellation, minimum terms, cancellation reasons |
| Billing | Idempotent engine with explicit billing periods, locked invoice snapshots, discounts (fixed/percent, product/plan/subscription scope, limits, windows), exclusive/inclusive tax, credit notes, adjustment and replacement invoices, PDF/print/email, bulk actions, dunning, retry of failed subscriptions |
| Money | Payments with statuses (received / awaiting confirmation / failed), customer-reported payments reconciled by staff, refunds, idempotency keys, optional Razorpay hosted checkout with verified, deduplicated webhooks; sandbox money never counts as revenue |
| Customers | Portal with subscriptions, invoices, PDF, add-on and plan changes, pause/cancel/renew where the plan allows, billing details, payment preferences; public storefront with checkout |
| Operations | Role + capability permissions, audit log, email outbox with retries, rule-based automation, notifications and tasks, rate limiting, backups, health check |
| Insight | Overview, revenue, recurring-revenue bridge (new / expansion / contraction / churn), retention cohorts, receivables aging, breakdown by plan/product/customer/salesperson, CSV and print |

## Get started

Requires Python 3.13. SQLite and console email work locally without external accounts.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8765
```

Open [Brightloop locally](http://127.0.0.1:8765). Prefer `uv`? Use `uv venv .venv` and `uv pip install -r requirements.txt` for the first two commands.

### Your demo team

These published accounts belong to the development dataset. Never seed them in production.

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@brightloop.test` | `Admin@12345` |
| Staff: sales + billing | `staff@brightloop.test` | `Staff@12345` |
| Staff: billing + finance | `finance@brightloop.test` | `Finance@12345` |
| Staff: sales + catalogue | `catalog@brightloop.test` | `Catalog@12345` |
| Customer (portal) | `maya@acme.test` | `Customer@123` |

The fresh demo contains **78 contracts across 38 organizations**, with varied seats, billing frequencies, payment histories, quotations, credits and refunds. Records use reserved test domains and must not be treated as real customer data.

Already running the original demo? `seed_demo --expand` adds the keyed **26-contract / 13-organization** extension, preserves existing records, and safely skips a completed expansion. It refuses unrecognized or conflicting data. Custom contracts remain in place, so your total may exceed 78.

`seed_demo --reset` wipes and rebuilds (development only). `seed_demo --bulk 5000` adds thousands of directly inserted subscriptions for performance testing.

### Try a complete workflow

1. Sign in as administrator and explore the catalogue, recurring plans and quotation templates.
2. Create a quotation for a customer, review the recurring charges, and send it for acceptance.
3. Accept through the customer portal, then run scheduled billing from a separate process.
4. Open the resulting invoice, record a partial payment, then settle the balance.
5. Preview a plan or add-on change and inspect its contract history and revenue movement.

The automated [browser journey](e2e/journey.py) exercises the commercial flow, persistence after restart, and customer access boundaries in a disposable database.

## Built around financial records

| Principle | Implementation |
|---|---|
| Exact amounts | Shared `Decimal` calculations, explicit rounding, tax and discount snapshots |
| Repeatable billing | Explicit covered periods, database uniqueness and atomic schedule advancement |
| Traceable changes | Effective-dated contract events, linked credits and adjustments |
| Protected access | Server-side capabilities, customer ownership checks, CSRF and audit events |
| Safe retries | Checkout receipts, payment identifiers and webhook deduplication |
| Clear revenue | Cash, invoices and recurring run rates reported separately; sandbox payments excluded |

Consequential operations live in domain services rather than templates. PostgreSQL is the production database; SQLite keeps local setup simple. See the [architecture and money policies](docs/ARCHITECTURE.md).

## Keep billing running

The web server does not execute scheduled billing. Run a separate scheduler for billing, automation and outbox delivery:

```cron
*/10 * * * *  cd /app && .venv/bin/python manage.py run_scheduled
30 2 * * *    cd /app && .venv/bin/python manage.py backup --keep 14
```

Or keep one process alive: `manage.py run_scheduled --every 300`. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for systemd units, restore, PostgreSQL and security notes.

## Connect your environment

Use [.env.example](.env.example) as a reference. Export values through your shell or process manager; Django does not automatically load that file.

- **Email:** set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`. Without `EMAIL_HOST` mail prints to the console and is **not delivered**. Every message goes through the outbox (Settings → Email outbox) so failures are visible and retried.
- **Database:** SQLite by default. For PostgreSQL: `uv pip install -r requirements-postgres.txt` and set `DATABASE_URL=postgres://user:pass@host:5432/db`.
- **Online payments:** set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`; add the webhook URL shown in Settings → Payments. Without keys the integration is shown as not configured and manual payment recording stays fully operational. No card data is ever stored.
- **Production:** `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`; run behind TLS. `Dockerfile` included.

## Verify it

```sh
.venv/bin/python manage.py test                           # backend, security, billing, payments and report regressions
DATABASE_URL=postgres://... .venv/bin/python manage.py test   # same suite on PostgreSQL (includes parallel-billing test)
python e2e/journey.py                                     # real-browser 26-step acceptance journey + restart persistence + mobile/tablet overflow
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py perf_check --cold              # page latency at the current dataset size
```

`e2e/journey.py` needs `playwright` (`uv pip install playwright`) and a Chromium (`CHROMIUM=/path`). It writes screenshots to `e2e/out/`.

Additional checks: `python e2e/interaction_regressions.py`, `python e2e/visual_refinement.py http://127.0.0.1:8765 final`, `python e2e/browser_performance.py`, and `python e2e/a11y_check.py --axe-script /path/to/axe.min.js` (supply a local axe-core distribution). These use the seeded demo server. Browser and accessibility findings are scoped to the pages and viewports each script visits.

Invoice density and checkbox states: `python e2e/layout_regressions.py`. Native print and downloadable PDF checks: `python e2e/print_regressions.py`; install Poppler (`pdfinfo`, `pdftotext`, `pdftoppm`) for pagination, text and raster inspection. PDF-specific backend checks skip when those tools are unavailable; HTML and authorization checks still run.

### Measured, with boundaries

On the documented local machine, the 5,052-subscription / 17,872-invoice dataset returned every tested server route within **215 ms**. The latest checkpoint passed applicable tests on both SQLite and PostgreSQL, alongside the browser journey and accessibility scans. These are local measurements, not production latency guarantees. Reproduce them with the commands in the [performance log](docs/PERFORMANCE.md).

If signup matches a billing profile prepared by staff, access is granted only after following a verification link sent to that email. The link expires after one hour; the verification page supports rate-limited resend. Configure SMTP for real delivery; local console/test messages are not delivered to an inbox.

## Documentation

- [Requirements and acceptance checks](docs/REQUIREMENTS.md) · [Architecture and policies](docs/ARCHITECTURE.md) · [Metric definitions](docs/METRICS.md)
- [UI/UX handoff](docs/HANDOFF_UIUX.md) · [Operations runbook](docs/OPERATIONS.md) · [Performance log](docs/PERFORMANCE.md) · [Implementation plan and known limits](docs/IMPLEMENTATION_PLAN.md)
- [Current 100-step frontend and 100-step backend refinement plan](docs/UIUX_BACKEND_200_STEP_PLAN.md)

---

**Before production:** configure TLS, PostgreSQL, SMTP, backups and the independent scheduler. Online checkout requires provider credentials; real SMTP delivery and a provider sandbox run remain environment-dependent checks. The tax model and operational revenue reports are not a statutory accounting system. Read the full [known limitations](docs/IMPLEMENTATION_PLAN.md#known-limitations-deliberate).
