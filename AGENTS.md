# Brightloop Studio — project instructions

Subscription business platform: Django 5.2 monolith, server-rendered templates, HTMX + Alpine (vendored in `static/vendor`), Chart.js.
Original Brightloop product only: never reference other subscription products in code, UI or docs.

- Run: `.venv/bin/python manage.py runserver 127.0.0.1:8765` (port 8000 may belong to another project). Tests: `manage.py test`. Browser journey: `python e2e/journey.py`.
- Money is `Decimal`, rounded half-up to 0.01 via `core.money.q`. Business rules live in `*/services.py`, never in views/templates.
- Every consequential change goes through a service and writes `core.audit.log` and, for contracts, a `SubscriptionChange`.
- Authorisation is enforced server-side: `core.permissions` decorators + ownership-scoped querysets in the portal.
- Commits are authored as `vyas-devgna`; no co-author trailers. Do not push unless the user asks.
- Progress and decisions: `docs/IMPLEMENTATION_PLAN.md`. Operations: `docs/OPERATIONS.md`.
