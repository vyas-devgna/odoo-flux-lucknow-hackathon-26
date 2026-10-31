# Proposed architecture

This design is a starting contract for the team, not a description of an existing application. The [official brief](https://hackathon.odoo.com/web/content/215872?download=true) takes precedence when it conflicts with a proposal here. Record any changed decisions in this document before teammates build against different assumptions.

## Stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Application | Python and Django 5.2 LTS | One codebase with forms, ORM, sessions, permissions, admin, and email support. |
| Interface | Django templates, responsive CSS, and small amounts of JavaScript | A single server renders the staff interface and customer portal. Bootstrap may be bundled locally if the team wants it. |
| Database | SQLite for the round | Easy local setup and persistent dynamic data. Each developer has their own local database. |
| Billing | Django management command, invoked by one system scheduler | A separate scheduled process runs even when nobody is viewing the website. |
| Email | Django email with SMTP | Real delivery is needed for reset links and invoice sending; console email is for local development only. |
| Invoice output | HTML and browser print styles | A printable invoice without a separate PDF service. |

Django 5.2.8 or later supports Python 3.14; pin a tested 5.2 patch release when the scaffold is added. See [Django 5.2 compatibility](https://docs.djangoproject.com/en/5.2/releases/5.2/). No paid service or API key is part of this plan.

```mermaid
flowchart LR
    Customer[Customer browser] --> Web[Django views and templates]
    Staff[Staff and admin browser] --> Web
    Web --> Auth[Sessions and permission checks]
    Web --> Services[Subscription and billing logic]
    Services --> DB[(SQLite)]
    Web --> DB
    Timer[System scheduler] --> Job[Billing management command]
    Job --> Services
    Web --> Mail[Configured SMTP server]
    Mail --> Inbox[Customer email]
```

The scheduler is a separate operating-system process. The web server alone does not start it. If the machine is off, overdue work runs when the scheduler resumes; a manual command can assist an operator but does not replace scheduling.

## Suggested code boundaries

These are proposed responsibilities, not files that already exist. A small team can use fewer Django apps if that makes integration easier; agree on shared models and service interfaces first.

| Area | Responsibility | Main collaborators |
| --- | --- | --- |
| Accounts | User model, signup, login, reset, roles, customer ownership | Portal and staff views |
| Catalogue | Products, variants, plans, taxes, quotation templates, discounts | Subscriptions and billing |
| Subscriptions | Customers, quotations, subscription lines, lifecycle, add-ons | Catalogue, billing, portal |
| Billing | Billing periods, invoices, discounts, tax calculations, payments | Subscriptions and reporting |
| Presentation | Staff screens, customer portal, finance summary, print layout | All areas through shared services |

Use server-side permission checks for every protected action and record. Django admin can accelerate internal configuration, but the customer portal needs its own ownership-filtered views. Do not depend on hidden buttons as authorization.

## Data relationships

The diagram shows conceptual relationships. It is not a final database schema: a quotation and subscription may share a record if the team can preserve the required states and fields clearly.

```mermaid
erDiagram
    USER o|--o| CUSTOMER : represents
    CUSTOMER ||--o{ SUBSCRIPTION : owns
    PLAN ||--o{ SUBSCRIPTION : governs
    PLAN ||--o{ QUOTATION_TEMPLATE : used_by
    QUOTATION_TEMPLATE ||--|{ TEMPLATE_LINE : contains
    PRODUCT ||--o{ VARIANT : has
    PRODUCT ||--o{ TEMPLATE_LINE : references
    SUBSCRIPTION ||--|{ SUBSCRIPTION_LINE : contains
    PRODUCT ||--o{ SUBSCRIPTION_LINE : references
    SUBSCRIPTION ||--o{ BILLING_PERIOD : covers
    BILLING_PERIOD ||--o{ INVOICE : produces
    INVOICE ||--|{ INVOICE_LINE : contains
    INVOICE ||--o{ PAYMENT : receives
    DISCOUNT_RULE }o--o{ PRODUCT : targets
    DISCOUNT_RULE }o--o{ SUBSCRIPTION : applies_to
```

Store plan configuration separately from each customer's subscription. A subscription line records the agreed product, variant, quantity, and price. An invoice stores a snapshot of its customer, period, lines, discount, tax, and totals so later catalogue changes do not rewrite past bills. Keep a record of discount use tied to the relevant invoice or billing period.

| Record | Minimum information to agree on |
| --- | --- |
| User and customer | Normalized unique email, password hash and role; customer identity and the optional linked login. Staff can prepare a quote before a customer has signed up. |
| Product and variant | Name, type, sale and cost prices, notes; variant label and price adjustment. |
| Plan | Price, billing interval, minimum quantity, availability dates, and close/pause/renew settings. |
| Tax and discount | Tax rule; discount type and value, eligibility threshold, dates, usage limit, and product or subscription scope. |
| Quotation template | Plan, default product lines, and validity in days. |
| Subscription and lines | Number, customer, plan, dates, payment terms, state, next due date; product, variant, quantity, unit price, tax and line amount. |
| Billing period and invoice | Subscription, covered dates and processing outcome; invoice number, state, due date, line and total snapshots, and any canceled/replacement relationship. |
| Discount use and payment | Applied rule, invoice or period and amount; invoice, payment method, amount, date, and note. |

The owner of each model should publish the final fields and migrations before another slice depends on them. Validate quantities, amounts, date ranges, and role changes on the server as well as in forms.

Create the custom user model and set `AUTH_USER_MODEL` **before the first migration**. Normalize email consistently and enforce uniqueness in the database as well as the form. Django recommends setting the custom model before the first migration. [Django custom-user guidance](https://docs.djangoproject.com/en/5.2/topics/auth/customizing/#using-a-custom-user-model-when-starting-a-project).

## Lifecycle and permissions

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Quotation
    Quotation --> Confirmed
    Confirmed --> Active
    Active --> Closed
    Active --> Paused: if plan allows
    Paused --> Active: resume if allowed
    Paused --> Closed: if plan allows
    Closed --> [*]
```

The five named states follow the brief. `Paused` is a proposed additional operational state; the team can use a flag instead. Define renewal as an explicit action that updates or creates a clearly linked subscription term; do not silently reopen a closed record. Plan permissions must be enforced on the server for staff and customer actions.

For invoices, retain the brief's Draft → Confirmed → Paid progression. Represent **Canceled** separately as a terminal state so it is not counted as outstanding. A partially paid invoice remains confirmed with a remaining balance; it becomes paid only when the balance is zero. These are proposed interpretations of areas the brief does not fully specify.

| Action | Admin | Internal staff | Customer |
| --- | --- | --- | --- |
| Configure products, plans and taxes | Yes | Limited by assigned permissions | No |
| Create discount rules and add staff | Yes | No | No |
| Handle quotations, invoices and payments | Yes | As permitted | Own records only where a customer action is provided |
| View and manage a subscription | Yes | As permitted | Own subscription, within plan rules |

The staff permission details are a proposal; the brief states that staff access is limited but does not enumerate every permission. Always check customer ownership in the query itself.

## Prices, discounts, and tax

Use integer minor units for stored monetary amounts; for the round, INR/paise is a proposed single-currency assumption, not a requirement in the brief. Calculate percentages with exact arithmetic and a documented rounding rule; do not use binary floating point for money. SQLite has no native exact decimal type and has limited write concurrency. [Django's SQLite notes](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes).

Proposed pricing rule: the plan price pre-fills the base subscription line; the chosen variant adjusts that line, and enabled add-ons have their own lines. The plan is not charged a second time as a separate fee. A quotation stores the agreed line prices, and generated invoices snapshot those values.

Proposed discount rule: attach one eligible discount to a subscription; it can target eligible product lines or the whole subscription. Calculate the discount before tax, never below zero, then calculate tax on the discounted lines. For a whole-subscription discount, allocate any fixed discount across eligible lines deterministically so the per-line taxes and displayed total agree. Document the rounding and allocation rules in the implementation. Stacking discounts is outside this initial policy.

```mermaid
flowchart LR
    A[Agreed product lines and quantities] --> B[Apply eligible discount]
    B --> C[Calculate tax on discounted lines]
    C --> D[Invoice total]
    D --> E[Subtract recorded payments]
    E --> F[Outstanding balance]
```

Check eligibility and available usage when an invoice is created for its billing date. Save the applied rule, calculated amount, and usage record with that invoice in the same transaction. Decide and document how cancellation and a replacement invoice affect a usage limit; neither a retry nor a replacement should silently consume the same use twice.

For a concrete calculation, assume a monthly plan line costs ₹1,000 per unit, a variant adds ₹560 per unit, quantity is two, a 10% subscription discount applies, and a hypothetical 18% tax applies to that line. The 18% rate is an example, not a tax rule prescribed by the brief.

| Step | Amount |
| --- | ---: |
| Two units at ₹1,560 | ₹3,120.00 |
| 10% discount | −₹312.00 |
| Discounted subtotal | ₹2,808.00 |
| 18% tax | ₹505.44 |
| Invoice total | ₹3,313.44 |
| Remaining after a ₹1,000 payment | ₹2,313.44 |

## Recurring billing

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant B as Billing command
    participant D as Database
    S->>B: Run on a regular schedule
    B->>D: Find eligible subscriptions with due periods
    loop Each due period
        B->>D: Begin short transaction
        B->>D: Check period, plan rules, discount and tax
        B->>D: Save invoice snapshot and advance schedule
        D-->>B: Commit together, or roll back on failure
    end
    B-->>S: Report created, already processed and failed periods
```

- Store a billing period and next due date for each subscription. Support daily, weekly, monthly, and yearly intervals.
- Use a database-backed uniqueness rule for an ordinary invoice per subscription and period, plus a transaction for the period, invoice, discount use, and next-due update. A retry must be harmless, including after a partial failure.
- A canceled invoice remains in the period history. Do not automatically recreate it on every scheduler run; make any replacement an explicit operation with a recorded link to the canceled invoice.
- Process overdue periods after downtime, without billing paused or closed subscriptions contrary to the chosen policy. Log failures so they can be retried and investigated.
- Use one scheduler for SQLite, prevent overlapping command runs, keep transactions short, and use bounded retries for lock errors. Database uniqueness and transactional checks remain necessary even with a single scheduler. SQLite does not implement `select_for_update()`; do not rely on it for locking. If concurrent writes become a problem, move the same Django models to PostgreSQL and revisit locking. [Django SQLite locking guidance](https://docs.djangoproject.com/en/5.2/ref/databases/#database-is-locked-errors).

Proposed calendar policy: use `Asia/Kolkata` for the round, treat dates consistently, and preserve the original day-of-month anchor. If a month has no matching day, use its last day and return to the original anchor when possible: for example, 31 January 2027 → 28 February → 31 March. Make expiry, renewal, pause, and resume behavior explicit before implementation. This is a team policy, not a rule given by the brief. [Stripe's billing-cycle examples](https://docs.stripe.com/billing/subscriptions/billing-cycle) illustrate the short-month case.

## Interfaces between slices

Agree on actual names and signatures when the scaffold exists. Each operation below should be implemented once on the server and reused by both the web interface and scheduled command where applicable.

| Operation | Input | Expected result |
| --- | --- | --- |
| Create a subscription from a template | Customer, template, dates, quantities and terms | Draft with agreed line snapshots and a unique subscription number. |
| Change subscription state | Subscription, requested action, actor | Allowed transition and schedule update, or a clear validation error. |
| Generate due invoices | Current time | Due periods processed once, failures reported, and missed periods eligible for later retry. |
| Record a payment | Invoice, amount, date, method, note and actor | Payment saved and remaining balance/status updated together. Reject overpayment under the proposed first-round policy. |
| Build finance summary | Date range and permitted filters | Active subscription count, cash received, outstanding and overdue amounts, plus recurring amounts grouped by billing frequency. |

Keep cash received, amounts invoiced, outstanding balances, and recurring amounts distinct. Report a precise definition for each number; do not label payments as recognized accounting revenue without an accounting policy.

## Security and operational boundaries

- Use Django's password hashing, sessions, CSRF protection, and password reset tools. Add a validator for the brief's password rules and enforce roles and ownership in server-side queries and actions. [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/).
- Use a configured SMTP server for real reset and invoice emails. The console backend only prints messages locally; do not present it as delivered email. Show or log delivery failures without exposing reset links to other users.
- Keep secrets in environment variables, never in the repo; keep `.env`, the local database, and virtual environments out of Git. Commit migrations and safe sample data needed for a fresh run.
- Store payment records only. The brief does not specify a gateway or require storing card details. Never store card details in this application.
- Index and paginate the queries used for due billing, customer records, invoice status/due date, and summaries. Measure response time with thousands of subscriptions; the chosen stack alone does not prove the under-two-second target.

## External references

- [Official problem statement](https://hackathon.odoo.com/web/content/215872?download=true)
- [Django database transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/)
- [Django email backends](https://docs.djangoproject.com/en/5.2/topics/email/)
- [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) and [password reset guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
