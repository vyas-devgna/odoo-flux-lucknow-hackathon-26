# Operator runbook

## Install
```sh
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt          # add -r requirements-postgres.txt for PostgreSQL
cp .env.example .env                                                            # edit, then export (systemd EnvironmentFile works)
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser                                     # the first admin (role is set automatically)
.venv/bin/gunicorn config.wsgi -b 127.0.0.1:8000 --workers 3
```
Put nginx/Caddy in front for TLS. With `DJANGO_DEBUG=0` the app refuses to start without `DJANGO_SECRET_KEY`, redirects to HTTPS, sets secure cookies and HSTS, and serves static files through WhiteNoise. `manage.py check --deploy` reports only the optional HSTS include-subdomains/preload settings. A `Dockerfile` is included (mount `/data` for SQLite).

## Scheduler (one scheduler only)
```
*/10 * * * *  cd /app && .venv/bin/python manage.py run_scheduled     # billing, then automation rules, then email outbox
30 2 * * *    cd /app && .venv/bin/python manage.py backup --keep 14  # SQLite online snapshot, or pg_dump -Fc for PostgreSQL
```
`run_scheduled` holds a file lock, so overlapping runs exit cleanly. After downtime the next run bills every missed period exactly once. systemd alternative:
```ini
# /etc/systemd/system/brightloop-scheduler.service
[Service] WorkingDirectory=/app  EnvironmentFile=/app/.env  ExecStart=/app/.venv/bin/python manage.py run_scheduled --every 300  Restart=always
```
Individual commands: `run_billing` (billing only), `send_outbox` (deliver pending mail), `seed_demo`, `perf_check`.

## Email
Set `EMAIL_HOST` (+ port/credentials). Every message is written to the outbox first; failures retry with exponential backoff up to 6 attempts and stay visible at **Settings → Email outbox** with a Retry button. Without `EMAIL_HOST` the console backend prints mail and nothing is delivered. Set `EMAIL_FQDN` to your sending domain (avoids a slow reverse-DNS lookup when stamping Message-IDs).

Delivery atomically claims a row as **Sending** before contacting SMTP. Concurrent workers cannot send the same pending row. A process interrupted during SMTP leaves the message in Sending because delivery may already have happened. After ten minutes, an administrator may use **Requeue after review**, only after checking the recipient or SMTP delivery log. SMTP cannot provide exactly-once delivery across an ambiguous network failure.

Signup never claims a staff-prepared customer profile just by matching its email. The customer follows an expiring emailed verification link to attach that profile. Resends are limited; console email in development is not real delivery.

## Online payments (optional)
`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. Add `https://<host>/billing/webhooks/razorpay/` in the provider dashboard for `payment_link.paid` and `payment.failed`. Use test keys first: those payments are flagged *test* and excluded from cash-received reports. If a payment's outcome is uncertain, open the invoice and use **Check status** on its payment link; nothing is recorded until the provider confirms capture.

## Backups and restore
SQLite: `backup` uses the online backup API (consistent while the app runs). To restore, stop the app, copy a snapshot over `DJANGO_DB`, start it. PostgreSQL: `backup` runs `pg_dump -Fc`; restore with `pg_restore -c -d <db> file.dump`. Test a restore regularly.

## Roles and capabilities
- **Admin:** everything, including team, discounts, company settings, payments settings, automation, audit log and outbox.
- **Staff** get any of: *Sales* (customers, quotations, contract changes), *Billing* (invoices, credit notes, billing runs), *Finance* (record payments and refunds, reports), *Catalogue* (products, plans, taxes, templates). They see read-only catalogue pages otherwise. Direct URLs and POSTs are checked server-side.
- **Customer:** only their own subscriptions, invoices, payments and profile.

## Monitoring
`GET /healthz` → `{"status":"ok"}` when the database answers. **Billing runs** lists every run and any subscription that failed, with a one-click retry. The **audit log** records who changed what. Logs go to stdout (`LOG_LEVEL`).

## Security notes
CSRF on all forms; the webhook is the only exempt endpoint and requires a valid HMAC signature. Passwords: more than 8 characters with upper, lower and a special character; PBKDF2 hashing. Sign-in, sign-up and password-reset are rate limited. Reset links expire after one hour and work once. CSV exports neutralise spreadsheet formulas. Upload size is capped. Secrets live only in the environment.

## Upgrades
`git pull`, install requirements, `manage.py migrate`, `collectstatic`, restart. Migrations are additive; take a backup first.
