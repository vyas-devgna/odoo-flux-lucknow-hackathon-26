# Performance log

Measured with `manage.py perf_check --cold` (full request/response cycle through Django's test client, report cache cleared before every request = worst case). Latencies are server-side medians of 3 runs.

**Environment:** Intel Core i5-11400H (12 threads), 15 GB RAM, Linux 7.2, Python 3.13, Django 5.2, single process, no reverse proxy.

| Dataset | Database | Subscriptions | Invoices | Payments | Slowest page | Everything else |
|---|---|---|---|---|---|---|
| Demo (`seed_demo`) | SQLite | 52 | 240 | 224 | < 100 ms | < 50 ms |
| `seed_demo --bulk 5000` | SQLite | 5,052 | 17,938 | 12,500 | 182 ms (MRR report) | median 15–160 ms |
| `seed_demo --bulk 5000` | PostgreSQL 16 | 5,052 | 17,938 | 12,500 | 196 ms | median 8–190 ms |
| `--bulk 5000` + `--bulk 15000` | SQLite | 20,052 | ~71,000 | ~50,000 | 677 ms (revenue report) | list pages 39–46 ms |

Budget: every page under 2 seconds. Largest measured worst case: 0.68 s at 20,000 subscriptions.

What keeps it fast: indexed filters (state, due date, issue date, customer), pagination of every list, `select_related`/`prefetch_related` on rows shown, month aggregates in SQL, a versioned cache for MRR/cohort series invalidated on writes, and tests that assert **query counts do not grow with row counts** (`QueryBudgets`). The billing run touches only due subscriptions (`state + next_invoice_date` index) and processes each in its own short transaction.

Reproduce: `seed_demo --bulk 5000 && perf_check --cold`.
