# Requirements and acceptance checks

The source of truth is the [Subscription Management System brief](https://hackathon.odoo.com/web/content/215872?download=true). Its four pages describe Brightloop Software and the work that happens during a billing month. The list below translates that brief into checks; it does not claim that any feature is implemented.

## The people using the system

| Person | What the brief says they need |
| --- | --- |
| Admin | Control the system; set up products, plans, taxes, and discounts; add staff. |
| Internal staff | Manage customers and the day-to-day quotation, subscription, invoice, and payment work with limited access. |
| Customer | Create an account and view and manage their own subscriptions and see their own invoices. |
| Finance lead | Review subscriptions, revenue, payments, and overdue invoices with filters and a summary. |

## Required product behavior

### 1. Accounts and access

- Customers sign up with an email and password. One email belongs to one account.
- Passwords are **more than eight characters** and contain an uppercase letter, a lowercase letter, and a special character.
- A forgotten-password link is sent to the registered email.
- Separate admin, internal staff, and customer access. Only an admin can add staff or create discount rules; customers can access only their own subscriptions and invoices.

**Check:** Reject a duplicate email and invalid passwords. Complete a reset through an email, and show that one customer cannot open another customer's records.

### 2. Products, plans, and add-ons

- Admins configure products with a name, type, sales price, cost price, and notes. Products can have variants that change the price.
- Plans have a price, a daily/weekly/monthly/yearly billing frequency, minimum quantity, and availability dates.
- Plan settings determine automatic closing at the end and whether early closing, pausing, and renewal are allowed.
- The company offers add-ons that customers can switch on and off.

**Check:** Create a product with a price-changing variant, offer it on a plan, and show the configured plan controls. Enable and disable an add-on and verify the change persists.

### 3. Quotations and subscriptions

- Create a quotation from a template containing the plan and product lines; templates specify a validity period in days.
- A subscription has its own number, customer, plan, start and expiry dates, and payment terms. Its lines show product, quantity, unit price, taxes, and amount.
- The team can see the required progression: **Draft → Quotation → Confirmed → Active → Closed**.

**Check:** Create a quotation from a template, show its validity and lines, and progress a subscription through the applicable states. Show pause and renewal when the plan permits them.

### 4. Invoices and taxes

- Generate invoices from subscriptions when they are due, including customer details, product lines, taxes, and totals.
- Admin-defined tax rules are calculated automatically.
- Staff can confirm, cancel, send, and print invoices. The brief names **Draft → Confirmed → Paid** as the invoice progression.
- Hundreds of subscriptions may be due together. Billing must not duplicate or skip a period.

**Check:** Generate due invoices for more than one subscription; rerun the billing process and confirm that it creates no duplicate for the same period. Confirm, send, and print an invoice, and demonstrate cancellation separately.

### 5. Discounts

- Admins create fixed-amount or percentage rules with a minimum purchase or quantity, start/end dates, and a usage limit.
- A rule applies to selected products or an entire subscription.
- An eligible rule applies automatically to the next invoice and stops applying when expired or exhausted.

**Check:** Show an eligible discount on a generated invoice and show that an expired or exhausted rule is not applied.

### 6. Payments and reporting

- Record each payment against the correct invoice with its method, amount, date, and note.
- Show paid, outstanding, and overdue invoices.
- Provide a finance summary and filters for active subscriptions, revenue, payments, and overdue invoices, including how much remains owed. The brief also identifies visibility into recurring revenue as a business need.
- With thousands of subscriptions, every page should respond in under two seconds.

**Check:** Record a payment, refresh the invoice and finance summary, and see the changed balance. Show an overdue invoice and measure page response times using a documented dataset and environment.

## Event requirements

The [event page](https://hackathon.odoo.com/event/odoo-x-flux-lucknow-hackathon-26-36/register) asks for dynamic data, a responsive and consistent interface, input validation, intuitive navigation, and participation in Git by the team. Static JSON is acceptable for initial prototyping only. The organizer's virtual-round email also asks for the latest code on `main`, individual commits and regular pushes, an evaluator collaborator, and an open-access demo video showing the functional flow.

## Decisions the brief leaves open

The [architecture](ARCHITECTURE.md) proposes initial answers so teammates can build consistently. They are design choices, not organizer requirements.

| Question | Why the team must decide |
| --- | --- |
| How do plan prices, product lines, variants, and add-ons combine? | Avoid charging the plan price and the same product price twice. |
| When do add-on and plan changes affect billing? | Mid-period changes may need a policy for the current invoice. |
| What happens on a short month, leap day, pause, renewal, or expiry? | The next billing date and covered period must be predictable. |
| Can discounts stack, and when is a use counted? | Totals and usage limits must remain consistent on retries. |
| How are cancellation, partial payment, and overpayment represented? | Invoice status, balances, and replacement invoices must agree. |
| What does each revenue number mean? | Cash received, outstanding balances, and recurring revenue are different measures. |

## Useful additions after the required flow

If time remains, consider renewal reminders, a billing-run history, an audit log, CSV export, search and pagination, an online payment sandbox, failed-payment handling, prorated changes, credit notes, and MRR/ARR/churn analysis. These are suggestions, not requirements in the brief. Search and pagination may also help meet the required performance target.
