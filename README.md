# Brightloop Studio

![Brightloop Studio — quotations, contracts and recurring billing in one Django workspace](docs/assets/brightloop-banner.svg)

Brightloop Studio follows a customer agreement from quotation and acceptance through
invoices, payments and renewal. Staff work in the Django application; customers use
an ownership-scoped portal and storefront.

[Preview](#product-preview) · [Workflow](#workflow) · [Features](#what-the-workspace-covers) ·
[Quick start](#quick-start) · [Verification](#verification) · [Operations](#operations)

## Product preview

![Brightloop Studio staff overview](docs/assets/overview.png)

<p align="center"><em>Staff overview · real application screenshot</em></p>

<details>
<summary>Customer portal preview</summary>

![Brightloop Studio customer portal on mobile](docs/assets/customer-portal.png)

</details>

## Workflow

```mermaid
flowchart LR
    A[Prepare quotation] --> B[Customer or staff accepts]
    B --> C[Active contract]
    C --> D{Next billing period}
    D --> E[Invoice snapshot]
    E --> F[Payment or reconciliation]
    F --> D
    E --> G[Credit or adjustment]
    G --> D
    C --> H[Renewal quotation]
    H --> B
```

## What the workspace covers

| Work area | Included workflows |
|---|---|
| Sales and catalogue | Customer records, products, priced variants, add-ons, taxes, billing plans, quantity tiers, quotation templates, revisions, alternatives and online acceptance. |
| Contracts | Draft through closed, plus paused and cancelled states; effective-dated history; seat and add-on previews; immediate or scheduled amendments; renewal and cancellation rules. |
| Billing and payments | Idempotent billing periods; locked invoice snapshots; tax and discount rules; credit notes, adjustments and replacements; partial payments, staff reconciliation, failed attempts and refunds. Optional hosted checkout uses verified provider events. |
| Customer access | Ownership-scoped portal for contracts, invoices and allowed plan actions; public storefront checkout; saved payment preferences. |
| Operations | Staff capabilities, audit history, email outbox with retries, automation, tasks, notifications, scheduled billing, backups and health checks. |
| Reporting | Cash and receivables, recurring-revenue movements, retention cohorts and breakdowns by plan, product, customer and salesperson; CSV and print output. |

## Quick start

Requires Python 3.13. SQLite and console email are sufficient for local development.
The app reads environment variables directly; it does not automatically load `.env`.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

export DJANGO_DEBUG=1
export SITE_URL=http://127.0.0.1:8765
# Optional: choose a different SQLite file.
# export DJANGO_DB="$PWD/db.sqlite3"

.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The `.env.example` file documents
available settings; export the values through your shell or process manager.

### Demo accounts

These published credentials are for the seeded development dataset only. Do not seed
them in production.

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@brightloop.test` | `Admin@12345` |
| Sales and billing | `staff@brightloop.test` | `Staff@12345` |
| Billing and finance | `finance@brightloop.test` | `Finance@12345` |
| Sales and catalogue | `catalog@brightloop.test` | `Catalog@12345` |
| Customer portal | `maya@acme.test` | `Customer@123` |

A fresh seed creates **78 contracts across 38 organizations**, with four billing
frequencies, varied seats, quotations, payment histories, credits and refunds. Demo
contacts use reserved `.test` domains and are synthetic.

For a database containing the recognized original demo, run:

```sh
.venv/bin/python manage.py seed_demo --expand
```

The additive expansion creates **26 contracts and 13 organizations**. It checks the
known seed customers and original contracts, uses stable scenario keys, preserves
existing and custom records, and does no global billing maintenance. Repeating it
does not add duplicates; partial or unrecognized seed data is refused. Existing
custom contracts remain, so the resulting total can exceed 78.

`seed_demo --reset` deletes and rebuilds business data and is limited to `DEBUG=1`.
Use it only with a disposable local database. `seed_demo --bulk N` is a performance
fixture generator; it inserts records directly and is not a billing-history replay.

### PostgreSQL

Install the optional driver and export a PostgreSQL URL before running migrations:

```sh
.venv/bin/python -m pip install -r requirements-postgres.txt
export DATABASE_URL='postgres://USER:PASSWORD@127.0.0.1:5432/brightloop'
.venv/bin/python manage.py migrate
```

Use a local disposable database for tests. Keep credentials in the process environment
or secret manager, never in the repository. See [`.env.example`](.env.example) and the
[operations guide](docs/OPERATIONS.md).

## Run a workflow

1. Sign in as staff and prepare a quotation from the catalogue.
2. Send it; the customer reviews and accepts it in the portal.
3. Run the scheduler to issue due invoices.
4. Record a payment or reconcile a customer-reported payment.
5. Preview a contract change and inspect its history and revenue movement.

The [browser journey](e2e/journey.py) starts against a disposable database and checks
the commercial flow, access boundaries, persistence after restart and mobile/tablet
overflow.

## Verification

```sh
.venv/bin/python manage.py test
DATABASE_URL='postgres://USER:PASSWORD@HOST:5432/DB' .venv/bin/python manage.py test
python e2e/journey.py
python e2e/a11y_check.py --axe-script /path/to/axe.min.js
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py perf_check --cold
```

Latest local run: **208 backend tests** on SQLite (4 skipped). The last recorded
PostgreSQL run, before the report-print tests were added, passed **202 tests** (1
backup-path skip). The browser journey passed **34/34 checks**, and **60 accessibility
scans** found no issues. The accessibility run covers the script's selected staff, customer
and public pages at two viewport widths and two themes; it is scoped evidence, not a
whole-site certification. It needs a local axe-core script and runs no third-party
code from the application.

The Chromium/Poppler print regression also passes: short invoices fit one page, long
invoices paginate with repeated headings, and report tables and charts fit landscape output.

At 5,052 subscriptions and 17,872 invoices, the largest response in the documented
26-route cold-report-cache check was **215 ms** on the local test machine. These are
reproducible local measurements, not production latency guarantees. See the
[performance log](docs/PERFORMANCE.md) for environment, browser timings and limits.

## Operations

The web server does not run billing on its own. Run one independent scheduler:

```cron
*/10 * * * *  cd /app && .venv/bin/python manage.py run_scheduled
30 2 * * *    cd /app && .venv/bin/python manage.py backup --keep 14
```

`run_scheduled` runs billing, automation and outbox delivery. Use
`manage.py run_scheduled --every 300` for a continuously running scheduler process.
Without `EMAIL_HOST`, the console backend prints messages; it does not deliver them.
Razorpay hosted checkout is optional and needs provider credentials and a configured
webhook. It has not been exercised against the provider's live sandbox.

For deployment, PostgreSQL, TLS, SMTP, backups, restores and payment-provider setup,
follow [production installation](docs/OPERATIONS.md#install),
[database backup and restore](docs/OPERATIONS.md#backups-and-restore),
[email and outbox](docs/OPERATIONS.md#email), and
[online payment setup](docs/OPERATIONS.md#online-payments-optional). Environment
settings are listed in [`.env.example`](.env.example); Django does not load it
automatically.

## Known limits

- Tax is a per-line model, not statutory GST/VAT compliance.
- INR is the only supported currency. Revenue reports are operating metrics, not an
  accounting ledger.
- Customers can save payment preferences; card tokenization and automatic charging
  are not implemented.
- Live SMTP delivery and a real provider-sandbox run depend on external credentials.

See the complete [known-limitations list](docs/IMPLEMENTATION_PLAN.md#known-limitations-deliberate)
and [architecture policies](docs/ARCHITECTURE.md).

## Documentation

[Requirements](docs/REQUIREMENTS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Metric definitions](docs/METRICS.md) · [Operations](docs/OPERATIONS.md) ·
[Performance log](docs/PERFORMANCE.md) · [UI/UX handoff](docs/HANDOFF_UIUX.md) ·
[Implementation status and known limits](docs/IMPLEMENTATION_PLAN.md)
