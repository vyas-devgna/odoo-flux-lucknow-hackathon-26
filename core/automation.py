"""Rule-driven automation: event triggers (fire) and scheduled triggers (run_scheduled).
Every execution is keyed in AutomationLog so re-runs and retries never duplicate an email, task or notification."""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from . import mail
from .models import AutomationLog, AutomationRule, CompanySettings, Notification, Task


class _Safe(dict):
    def __missing__(self, key):
        return ""


def _context(obj):
    co = CompanySettings.get()
    cur = settings.CURRENCY_SYMBOL
    typ = type(obj).__name__
    if typ == "Invoice":
        return {"customer": obj.bill_to_name, "number": obj.number, "amount": f"{cur}{obj.balance if obj.balance else obj.total}",
                "due_date": f"{obj.due_date:%d %b %Y}", "plan": obj.subscription.plan.name, "company": co.name,
                "link": settings.SITE_URL + reverse("core:portal_invoice", args=[obj.pk])}, obj.bill_to_email, obj.customer, f"/billing/invoices/{obj.pk}/"
    return {"customer": obj.customer.name, "number": obj.number, "amount": f"{cur}{obj.total}",
            "due_date": f"{(obj.expiry_date if obj.state != 'quotation' else obj.quote_valid_until) or timezone.localdate():%d %b %Y}",
            "plan": obj.plan.name, "company": co.name,
            "link": settings.SITE_URL + reverse("core:portal_subscription", args=[obj.pk])}, obj.customer.email, obj.customer, f"/subscriptions/{obj.pk}/"


def _execute(rule, obj, bucket):
    ctx, email, customer, url = _context(obj)
    try:
        with transaction.atomic():  # savepoint: a duplicate must not poison an outer transaction
            AutomationLog.objects.create(rule=rule, target_type=type(obj).__name__, target_id=obj.pk, bucket=bucket)
    except IntegrityError:  # already ran for this record and bucket
        return False
    label = f"{rule.name}: {ctx['number']}"
    if rule.action == "email_customer" and rule.template:
        mail.queue_email(email, rule.template.subject.format_map(_Safe(ctx)), rule.template.body.format_map(_Safe(ctx)),
                         dedupe_key=f"auto:{rule.pk}:{type(obj).__name__}:{obj.pk}:{bucket}", related=obj)
    elif rule.action == "notify_staff":
        Notification.objects.create(text=label[:240], url=url)
    elif rule.action == "create_task":
        Task.objects.create(title=label[:200], url=url, due_date=timezone.localdate() + timedelta(days=2))
    return True


def fire(trigger, obj, *, bucket="once"):
    """Run every enabled rule for an event on `obj`. Safe to call repeatedly."""
    n = 0
    for rule in AutomationRule.objects.filter(trigger=trigger, enabled=True).select_related("template"):
        n += _execute(rule, obj, bucket)
    return n


def run_scheduled(as_of=None):
    """Evaluate time-based triggers. Returns {trigger: executions}."""
    from billing.models import Invoice
    from subscriptions.models import Subscription
    today = as_of or timezone.localdate()
    out = {}
    rules = list(AutomationRule.objects.filter(enabled=True, trigger__in=["renewal_upcoming", "invoice_due_soon", "invoice_overdue", "quotation_expiring"]).select_related("template"))
    for rule in rules:
        n = 0
        if rule.trigger == "renewal_upcoming":
            target = today + timedelta(days=rule.offset_days)
            qs = Subscription.objects.filter(state="active", expiry_date__gte=today, expiry_date__lte=target).select_related("customer", "plan")
            for s in qs:
                n += _execute(rule, s, "once")
        elif rule.trigger == "quotation_expiring":
            qs = Subscription.objects.filter(state="quotation", quote_valid_until__gte=today, quote_valid_until__lte=today + timedelta(days=rule.offset_days)).select_related("customer", "plan")
            for s in qs:
                n += _execute(rule, s, "once")
        elif rule.trigger == "invoice_due_soon":
            qs = Invoice.objects.filter(state="confirmed", due_date__gte=today, due_date__lte=today + timedelta(days=rule.offset_days)).exclude(kind="credit_note").select_related("subscription__plan")
            for i in qs:
                n += _execute(rule, i, "once")
        elif rule.trigger == "invoice_overdue":
            qs = (Invoice.objects.filter(state="confirmed", due_date__lte=today - timedelta(days=rule.offset_days))
                  .filter(total__gt=F("amount_paid") + F("credited_total")).exclude(kind="credit_note").select_related("subscription__plan"))
            for i in qs:
                days = (today - i.due_date).days
                bucket = f"d{days // rule.repeat_days}" if rule.repeat_days else "once"
                n += _execute(rule, i, bucket)
        out[rule.trigger] = out.get(rule.trigger, 0) + n
    return out
