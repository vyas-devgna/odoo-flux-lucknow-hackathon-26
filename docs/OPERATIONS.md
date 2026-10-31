# Operator runbook

## Install
```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # edit, then export the variables (systemd EnvironmentFile works)
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser   # first admin (role is set to admin automatically)
.venv/bin/gunicorn config.wsgi -b 127.0.0.1:8000 --workers 3
```
Put nginx/Caddy in front for TLS. With `DJANGO_DEBUG=0` the app refuses to start without `DJANGO_SECRET_KEY`, forces HTTPS, and sets secure cookies and HSTS. Or use the `Dockerfile` (mount `/data`).

## Scheduled jobs (one scheduler only)
```
*/15 * * * *  cd /app && .venv/bin/python manage.py run_billing      # invoices every due period, idempotent, catches up
0 9 * * *     cd /app && .venv/bin/python manage.py send_reminders   # overdue reminders + renewal notices
30 2 * * *    cd /app && .venv/bin/python manage.py backup --keep 14 # online SQLite snapshot
```
`run_billing` holds a file lock, so overlapping runs exit cleanly. If the machine was off, the next run bills every missed period once.

## Restore
Stop the app, copy a file from `backups/` over the database path (`DJANGO_DB`), start the app. Test restores regularly.

## Roles
- **Admin:** everything, including team, discounts, taxes, plans, company settings and the audit log.
- **Staff:** customers, quotations, subscriptions, invoices, payments, reports. Read-only on the catalogue.
- **Customer:** own subscriptions, invoices, add-ons, billing details; no access to any other record.

## Policies
Amounts are `Decimal`, rounded half-up to paise. Billing anchors to the start day (31 Jan → 28 Feb → 31 Mar). One best discount per invoice, applied before tax; cancelling an invoice returns its use. Overpayment is rejected; partial payments keep the invoice Confirmed. Seat/term edits on a live subscription and add-on toggles apply from the next invoice; issued invoices never change. Failed sign-ins are throttled (5 per email per 15 min).

## Health and monitoring
`GET /healthz` returns `{"status":"ok"}` when the database is reachable. Billing run results are on **Billing runs** and in the audit log; failures list per subscription and are retried on the next run.

## Known limits
No online card payments, proration, credit notes/refunds, multi-currency, or accounting ledger. SQLite is right for one server and tens of staff; move to PostgreSQL beyond that (swap `DATABASES`, add a driver, `pg_dump` for backups).
