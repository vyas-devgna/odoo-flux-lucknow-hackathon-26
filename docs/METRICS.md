# Metric definitions

Every number in the dashboard, reports and exports uses exactly one of these definitions (`core/reports.py`).

| Metric | Definition |
|---|---|
| **Cash received** | Sum of payments with status *Received*, `is_test = false`, dated in the range. Refunds are negative payments and net off. Failed and awaiting-confirmation payments never count. Test (sandbox) payments are reported separately. |
| **Invoiced** | Sum of non-cancelled documents issued in the range. Credit notes are negative, so invoiced is net of credits. |
| **Outstanding** | `total − credited − paid` on confirmed invoices (excluding credit notes), as of today. |
| **Overdue** | Outstanding on confirmed invoices whose due date is before today. |
| **Aging** | Outstanding bucketed by days past due: not yet due, 1–30, 31–60, 61–90, 90+. |
| **MRR** | Sum of `mrr_after − mrr_before` over all contract changes effective on or before the date. A contract's MRR is its **recurring** lines' pre-tax amount × the plan's periods-per-month factor (daily 30.4375, weekly 4.348214, monthly 1, yearly 1/12). One-time lines are excluded. Paused contracts contribute 0. |
| **ARR** | MRR × 12. |
| **New / Expansion / Contraction / Churn** | Movement of a contract change: first activation; MRR increase; MRR decrease; closure. *Paused* and *Reactivation* (resume) are shown separately. *Renewal* movements (old term closing, new term starting) net to zero and are excluded from new and churn. |
| **Logo churn** | Contracts active at the start of the period that closed inside it ÷ contracts active at the start. Contracts that joined and left inside the same period are excluded so the rate cannot exceed 100%. |
| **Revenue churn** | MRR lost from that same opening base ÷ opening MRR, capped at 100%. |
| **Retention (cohorts)** | Customers grouped by month of first activation; a customer is retained at month *k* if any of their contracts is active at that month end. |
| **Net revenue by dimension** | Invoice-line amounts after discounts and before tax, by plan, product, customer or salesperson, for non-cancelled documents issued in the range. |

Caching: aggregate series are cached for up to 60–120 seconds and invalidated whenever a contract, invoice or payment changes in this process.
