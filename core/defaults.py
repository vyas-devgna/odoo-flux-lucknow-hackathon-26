"""Starter email templates and automation rules, created once per database and never overwritten."""
from .models import AutomationRule, EmailTemplate

TEMPLATES = {
    "welcome": ("Subscription activated", "Your {plan} subscription {number} is active",
                "Hello {customer},\n\nYour {plan} subscription {number} is now active. You can review it, change seats and download invoices in your portal:\n{link}\n\nThank you,\n{company}"),
    "renewal": ("Renewal notice", "Your subscription {number} renews soon",
                "Hello {customer},\n\nYour {plan} subscription {number} reaches the end of its term on {due_date}. Review or renew it here:\n{link}\n\nThank you,\n{company}"),
    "due_soon": ("Invoice due soon", "Invoice {number} is due on {due_date}",
                 "Hello {customer},\n\nA friendly reminder that invoice {number} for {amount} is due on {due_date}.\n{link}\n\nThank you,\n{company}"),
    "overdue": ("Overdue reminder", "Reminder: invoice {number} is overdue",
                "Hello {customer},\n\nInvoice {number} was due on {due_date} and {amount} is still outstanding. If you have already paid, please ignore this note.\n{link}\n\nThank you,\n{company}"),
    "payment_failed": ("Payment failed", "We couldn't process your payment for {number}",
                       "Hello {customer},\n\nYour payment for invoice {number} ({amount}) did not go through. Please try again or use another method:\n{link}\n\nThank you,\n{company}"),
    "closed": ("Subscription closed", "Your subscription {number} has ended",
               "Hello {customer},\n\nYour {plan} subscription {number} is now closed. We'd love to know how we could have done better, and you can restart any time:\n{link}\n\nThank you,\n{company}"),
    "plan_changed": ("Plan changed", "Your subscription {number} was updated",
                     "Hello {customer},\n\nYour {plan} subscription {number} was updated. The new recurring amount is {amount}. Details:\n{link}\n\nThank you,\n{company}"),
    "quote_expiring": ("Quotation expiring", "Quotation {number} expires on {due_date}",
                       "Hello {customer},\n\nQuotation {number} expires on {due_date}. Review and accept it here:\n{link}\n\nThank you,\n{company}"),
}

RULES = [
    ("Welcome email on activation", "subscription_activated", "email_customer", "welcome", 0, 0),
    ("Renewal notice 14 days before", "renewal_upcoming", "email_customer", "renewal", 14, 0),
    ("Heads-up 3 days before invoice is due", "invoice_due_soon", "email_customer", "due_soon", 3, 0),
    ("Overdue reminder every 7 days", "invoice_overdue", "email_customer", "overdue", 1, 7),
    ("Tell staff about failed payments", "payment_failed", "notify_staff", None, 0, 0),
    ("Email customer about failed payment", "payment_failed", "email_customer", "payment_failed", 0, 0),
    ("Follow up on closed subscriptions", "subscription_closed", "create_task", None, 0, 0),
    ("Email customer when plan changes", "plan_changed", "email_customer", "plan_changed", 0, 0),
    ("Quotation expiring in 3 days", "quotation_expiring", "email_customer", "quote_expiring", 3, 0),
]


def ensure_defaults(**kwargs):
    if EmailTemplate.objects.exists() or AutomationRule.objects.exists():
        return
    tpls = {k: EmailTemplate.objects.create(key=k, name=n, subject=s, body=b) for k, (n, s, b) in TEMPLATES.items()}
    for name, trig, act, tkey, off, rep in RULES:
        AutomationRule.objects.create(name=name, trigger=trig, action=act, template=tpls.get(tkey), offset_days=off, repeat_days=rep)
