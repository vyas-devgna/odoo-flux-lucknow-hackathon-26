"""Invoice documents: creation helpers, state changes, credit notes, replacements, emailing."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from catalog.models import Discount
from core import audit, mail
from core.models import CompanySettings
from core.money import ZERO, q
from subscriptions.models import add_interval

from . import pricing
from .models import BillingPeriod, DiscountUse, Invoice, InvoiceLine


class BillingError(Exception):
    pass


def _snapshot(sub):
    c = sub.customer
    return dict(customer=c, bill_to_name=c.display_name, bill_to_email=c.email, bill_to_address=c.address, bill_to_tax_id=c.tax_id)


def period_bounds(sub, index):
    start = add_interval(sub.start_date, sub.plan.interval, index)
    end = add_interval(sub.start_date, sub.plan.interval, index + 1) - timedelta(days=1)
    if sub.expiry_date and end > sub.expiry_date:
        end = sub.expiry_date
    return start, end


def create_invoice_for_period(sub, index, on, *, replaces=None):
    """Create the invoice for period `index`. Caller owns the transaction. Discount use is claimed atomically."""
    start, end = period_bounds(sub, index)
    rule, rows, subtotal, d_total, tax_total = pricing.price(sub, index, on)
    if rule:  # claim a use; if it was taken meanwhile, price again without it
        from django.db.models import Q
        claimed = (Discount.objects.filter(pk=rule.pk).filter(Q(usage_limit__isnull=True) | Q(times_used__lt=F("usage_limit")))
                   .update(times_used=F("times_used") + 1))
        if not claimed:
            rule, rows, subtotal, d_total, tax_total = pricing.price(sub, index, on, use_discount=False)
    inv = Invoice.objects.create(
        subscription=sub, period_start=start, period_end=end, issue_date=on, due_date=on + timedelta(days=sub.payment_term_days),
        subtotal=subtotal, discount_total=d_total, tax_total=tax_total, total=subtotal - d_total + tax_total,
        discount_label=f"{rule.name} ({rule.label})" if rule else "", replaces=replaces, **_snapshot(sub))
    InvoiceLine.objects.bulk_create([InvoiceLine(invoice=inv, **r) for r in rows])
    if rule:
        DiscountUse.objects.create(discount=rule, invoice=inv, amount=d_total)
    BillingPeriod.objects.update_or_create(subscription=sub, index=index, defaults=dict(start=start, end=end, invoice=inv))
    return inv


def create_first_invoice(sub, actor=None):
    """Quote accepted on a pay-to-activate plan: bill period 0 now (confirmed, due immediately) and wait for payment."""
    from subscriptions.models import Subscription
    today = timezone.localdate()
    with transaction.atomic():
        if BillingPeriod.objects.filter(subscription=sub, index=0).exists():
            return BillingPeriod.objects.get(subscription=sub, index=0).invoice
        inv = create_invoice_for_period(sub, 0, today)
        Invoice.objects.filter(pk=inv.pk).update(due_date=today)
        inv.refresh_from_db()
        confirm_invoice(inv)
        Subscription.objects.filter(pk=sub.pk).update(periods_billed=1)
    audit.log(actor, "invoice.first", inv, f"awaiting payment to activate {sub.number}")
    send_invoice(inv, quiet=True)
    return inv


def create_adjustment_invoice(sub, diffs, period, *, today=None, actor=None):
    """Draft invoice for the prorated increase after a mid-period change. `diffs` come from subscriptions.services.preview_change."""
    today = today or timezone.localdate()
    _, p_start, p_end = period
    subtotal = sum((d["amount"] for d in diffs), ZERO)
    tax_total = sum((d["tax"] for d in diffs), ZERO)
    with transaction.atomic():
        inv = Invoice.objects.create(
            subscription=sub, kind=Invoice.ADJUSTMENT, period_start=today, period_end=p_end, issue_date=today,
            due_date=today + timedelta(days=sub.payment_term_days), subtotal=subtotal, tax_total=tax_total, total=subtotal + tax_total, **_snapshot(sub))
        InvoiceLine.objects.bulk_create([InvoiceLine(invoice=inv, product=d.get("product"), description=d["description"], quantity=1, unit_price=d["amount"],
                                                     tax_name=d["tax_name"], tax_rate=d["tax_rate"], tax_amount=d["tax"], amount=d["amount"]) for d in diffs])
    audit.log(actor, "invoice.adjustment", inv, f"prorated change on {sub.number}")
    return inv


# -------------------------------------------------------------- state changes
def confirm_invoice(inv):
    if inv.state != Invoice.DRAFT:
        raise BillingError("Only draft invoices can be confirmed.")
    inv.state = Invoice.CONFIRMED
    inv.finalized_at = timezone.now()
    inv.save(update_fields=["state", "finalized_at"])


@transaction.atomic
def cancel_invoice(inv):
    inv = Invoice.objects.get(pk=inv.pk)
    if inv.state not in (Invoice.DRAFT, Invoice.CONFIRMED) or inv.payments.filter(status="succeeded").exists() or inv.credited_total:
        raise BillingError("Only unpaid, uncredited draft or confirmed invoices can be cancelled.")
    inv.state = Invoice.CANCELLED
    inv.save(update_fields=["state"])
    use = DiscountUse.objects.filter(invoice=inv, released=False).first()
    if use:  # the discount use is given back; a replacement invoice claims it again if still eligible
        Discount.objects.filter(pk=use.discount_id, times_used__gt=0).update(times_used=F("times_used") - 1)
        use.released = True
        use.save(update_fields=["released"])


@transaction.atomic
def replace_invoice(inv):
    inv = Invoice.objects.get(pk=inv.pk)
    if inv.state != Invoice.CANCELLED or hasattr(inv, "replacement") or inv.kind != Invoice.RECURRING:
        raise BillingError("Only a cancelled recurring invoice without a replacement can be replaced.")
    sub = inv.subscription
    period = BillingPeriod.objects.filter(subscription=sub, start=inv.period_start).first()
    idx = period.index if period else 0
    return create_invoice_for_period(sub, idx, timezone.localdate(), replaces=inv)


@transaction.atomic
def issue_credit_note(inv, *, amount, reason="", actor=None, cap=False):
    """Linked correction document. The original stays untouched; its credited total grows and its balance shrinks."""
    inv = Invoice.objects.get(pk=inv.pk)
    if inv.kind == Invoice.CREDIT_NOTE or inv.state not in (Invoice.CONFIRMED, Invoice.PAID):
        raise BillingError("Credit notes can be issued only against confirmed or paid invoices.")
    amount = q(amount)
    remaining = inv.total - inv.credited_total
    if cap:
        amount = min(amount, remaining)
    if amount <= 0:
        raise BillingError("Credit amount must be positive.")
    if amount > remaining:
        raise BillingError(f"Cannot credit more than the uncredited total ({settings.CURRENCY_SYMBOL}{remaining}).")
    net = q(amount * (inv.total - inv.tax_total) / inv.total) if inv.total else amount
    tax = amount - net
    rate = q(inv.tax_total * 100 / (inv.total - inv.tax_total)) if inv.total - inv.tax_total else 0
    today = timezone.localdate()
    cn = Invoice.objects.create(
        subscription=inv.subscription, kind=Invoice.CREDIT_NOTE, state=Invoice.PAID, credit_for=inv, period_start=inv.period_start,
        period_end=inv.period_end, issue_date=today, due_date=today, subtotal=-net, tax_total=-tax, total=-amount, finalized_at=timezone.now(),
        bill_to_name=inv.bill_to_name, bill_to_email=inv.bill_to_email, bill_to_address=inv.bill_to_address, bill_to_tax_id=inv.bill_to_tax_id,
        customer=inv.customer)
    InvoiceLine.objects.create(invoice=cn, description=f"Credit against {inv.number}" + (f": {reason}" if reason else ""), quantity=1,
                               unit_price=-net, tax_name="", tax_rate=rate, tax_amount=-tax, amount=-net)
    inv.credited_total += amount
    if inv.state == Invoice.CONFIRMED and inv.balance <= 0:
        inv.state = Invoice.PAID
    inv.save(update_fields=["credited_total", "state"])
    audit.log(actor, "invoice.credit_note", inv, f"{cn.number} for {amount}")
    return cn


# -------------------------------------------------------------- email / print
def send_invoice(inv, request=None, quiet=False):
    if inv.state == Invoice.CANCELLED:
        raise BillingError("A cancelled invoice cannot be sent.")
    if inv.state == Invoice.DRAFT:
        confirm_invoice(inv)
    co = CompanySettings.get()
    link = settings.SITE_URL + reverse("core:portal_invoice", args=[inv.pk])
    pay = f"\nHow to pay:\n{co.bank_details}\n" if co.bank_details else ""
    body = (f"Hello {inv.bill_to_name},\n\nInvoice {inv.number} for {inv.period_start:%d %b %Y} – {inv.period_end:%d %b %Y} is ready.\n"
            f"Total: {settings.CURRENCY_SYMBOL}{inv.total}   Due: {inv.due_date:%d %b %Y}\n\nView, print or pay online: {link}\n{pay}\nThank you,\n{co.name}")
    key = f"invoice:{inv.pk}:{(inv.sent_at.isoformat() if inv.sent_at else 'first')}"
    mail.queue_email(inv.bill_to_email, f"Invoice {inv.number} from {co.name}", body, dedupe_key=key, related=inv)
    inv.sent_at = timezone.now()
    inv.save(update_fields=["sent_at"])


def send_reminder(inv, user=None):
    """Manual dunning email using the editable 'overdue' template. The counter only moves if the message was queued."""
    if not inv.is_overdue:
        raise BillingError("Only overdue invoices can be chased.")
    from core.models import EmailTemplate
    from core.automation import _Safe, _context
    tpl = EmailTemplate.objects.filter(key="overdue").first()
    ctx, email, _, _ = _context(inv)
    subject = (tpl.subject if tpl else "Reminder: invoice {number} is overdue").format_map(_Safe(ctx))
    body = (tpl.body if tpl else "Invoice {number} is overdue ({amount}).").format_map(_Safe(ctx))
    row = mail.queue_email(email, subject, body, dedupe_key=f"remind:{inv.pk}:{inv.reminder_count + 1}", related=inv)
    if row is None:
        raise BillingError("A reminder is already queued.")
    inv.reminder_count += 1
    inv.last_reminded_at = timezone.now()
    inv.save(update_fields=["reminder_count", "last_reminded_at"])
    audit.log(user, "invoice.remind", inv, f"reminder #{inv.reminder_count}")
