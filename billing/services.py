"""Billing engine. Used by the web UI and by the `run_billing` management command, so they cannot diverge."""
import traceback
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from catalog.models import Discount
from core.money import ZERO, q
from subscriptions.models import Subscription, add_interval

from .models import BillingRun, DiscountUse, Invoice, InvoiceLine, Payment


class BillingError(Exception):
    pass


# ---------- pricing ----------
def _best_discount(lines, on):
    """Pick the single best eligible rule (stacking is out of policy). Returns (discount, amount, allocation)."""
    best = None
    today_rules = (Discount.objects.filter(active=True, start_date__lte=on)
                   .filter(Q(end_date__isnull=True) | Q(end_date__gte=on))
                   .filter(Q(usage_limit__isnull=True) | Q(times_used__lt=F("usage_limit")))
                   .prefetch_related("products").order_by("id"))
    for rule in today_rules:
        scope = {p.pk for p in rule.products.all()} if rule.scope == Discount.PRODUCTS else set()
        if rule.scope == Discount.PRODUCTS and not scope:
            continue
        eligible = [l for l in lines if not scope or (l.product_id in scope)]
        base = sum((l.amount for l in eligible), ZERO)
        units = sum(l.quantity for l in eligible)
        if base <= 0 or base < rule.min_purchase or units < rule.min_quantity:
            continue
        raw = rule.value if rule.kind == Discount.FIXED else base * rule.value / 100
        amount = min(q(raw), base)
        if amount > 0 and (best is None or amount > best[1]):
            best = (rule, amount, eligible, base)
    if not best:
        return None, ZERO, {}
    rule, amount, eligible, base = best
    alloc, left = {}, amount
    for i, l in enumerate(eligible):
        share = left if i == len(eligible) - 1 else min(q(amount * l.amount / base), left)
        alloc[l.pk] = share
        left -= share
    return rule, amount, alloc


def _price(sub, on, use_discount=True):
    lines = list(sub.lines.select_related("product", "variant", "tax"))
    rule, d_total, alloc = _best_discount(lines, on) if use_discount else (None, ZERO, {})
    rows, subtotal, tax_total = [], ZERO, ZERO
    for l in lines:
        disc = alloc.get(l.pk, ZERO)
        net = l.amount - disc
        tax = q(net * l.tax_rate / 100)
        rows.append(dict(description=l.description, quantity=l.quantity, unit_price=l.unit_price, discount=disc,
                         tax_name=l.tax.name if l.tax_id else "", tax_rate=l.tax_rate, tax_amount=tax, amount=net))
        subtotal += l.amount
        tax_total += tax
    return rule, rows, subtotal, d_total, tax_total


def create_invoice_for_period(sub, index, on, *, replaces=None):
    """Create the invoice for period `index` of `sub`. Caller owns the transaction."""
    start = add_interval(sub.start_date, sub.plan.interval, index)
    end = add_interval(sub.start_date, sub.plan.interval, index + 1) - timedelta(days=1)
    if sub.expiry_date and end > sub.expiry_date:
        end = sub.expiry_date
    rule, rows, subtotal, d_total, tax_total = _price(sub, on)
    if rule:  # claim a use atomically; if it was taken meanwhile, price again without it
        claimed = (Discount.objects.filter(pk=rule.pk).filter(Q(usage_limit__isnull=True) | Q(times_used__lt=F("usage_limit")))
                   .update(times_used=F("times_used") + 1))
        if not claimed:
            rule, rows, subtotal, d_total, tax_total = _price(sub, on, use_discount=False)
    c = sub.customer
    inv = Invoice.objects.create(
        subscription=sub, customer=c, period_start=start, period_end=end, issue_date=on,
        due_date=on + timedelta(days=sub.payment_term_days), bill_to_name=c.display_name, bill_to_email=c.email,
        bill_to_address=c.address, bill_to_tax_id=c.tax_id, subtotal=subtotal, discount_total=d_total,
        tax_total=tax_total, total=subtotal - d_total + tax_total,
        discount_label=f"{rule.name} ({rule.label})" if rule else "", replaces=replaces)
    InvoiceLine.objects.bulk_create([InvoiceLine(invoice=inv, **r) for r in rows])
    if rule:
        DiscountUse.objects.create(discount=rule, invoice=inv, amount=d_total)
    return inv


def run_billing(as_of=None, trigger="scheduler"):
    """Invoice every due period exactly once, catching up after downtime. Idempotent and safe to re-run."""
    as_of = as_of or timezone.localdate()
    run = BillingRun.objects.create(as_of=as_of, trigger=trigger)
    errors = []
    new_invoices = []
    due_ids = list(Subscription.objects.filter(state=Subscription.ACTIVE, next_invoice_date__lte=as_of)
                   .order_by("id").values_list("id", flat=True))
    for sid in due_ids:
        while True:
            try:
                with transaction.atomic():
                    sub = Subscription.objects.select_related("plan", "customer").get(pk=sid)
                    due = sub.next_invoice_date
                    if sub.state != Subscription.ACTIVE or not due or due > as_of or (sub.expiry_date and due > sub.expiry_date):
                        break
                    exists = Invoice.objects.filter(subscription=sub, period_start=due, replaces__isnull=True, kind=Invoice.RECURRING).exists()
                    if exists:
                        run.skipped += 1  # already billed (e.g. an earlier run died after the invoice, before the schedule moved)
                    else:
                        new_invoices.append(create_invoice_for_period(sub, sub.periods_billed, as_of).pk)
                        run.created += 1
                    sub.periods_billed += 1
                    sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
                    sub.save(update_fields=["periods_billed", "next_invoice_date"])
            except Exception:  # one bad subscription must not stop the run; it is retried next time
                run.failed += 1
                errors.append(f"{sid}: {traceback.format_exc(limit=2)}")
                break
    from core.models import CompanySettings
    co = CompanySettings.get()
    if new_invoices and (co.auto_confirm_invoices or co.auto_email_invoices):
        for inv in Invoice.objects.filter(pk__in=new_invoices):
            try:
                if co.auto_email_invoices:
                    send_invoice(inv)  # confirms first, then emails
                else:
                    confirm_invoice(inv)
            except Exception:
                run.failed += 1
                errors.append(f"{inv.number}: {traceback.format_exc(limit=1)}")
    # Close finished terms: all periods up to expiry billed, and plan closes itself (or a renewal took over)
    finished = Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__lt=as_of).select_related("plan")
    for sub in finished:
        if sub.next_invoice_date and sub.next_invoice_date <= sub.expiry_date:
            continue
        if sub.plan.auto_close or sub.renewals.exists():
            sub.state, sub.closed_at, sub.next_invoice_date = Subscription.CLOSED, timezone.now(), None
            sub.save(update_fields=["state", "closed_at", "next_invoice_date"])
            run.closed += 1
    from core import audit
    audit.log(None, "billing.run", detail=f"{trigger}: created {run.created}, skipped {run.skipped}, failed {run.failed}, closed {run.closed}")
    run.errors = "\n".join(errors)[:5000]
    run.finished_at = timezone.now()
    run.save()
    return run


# ---------- invoice actions ----------
def confirm_invoice(inv):
    if inv.state != Invoice.DRAFT:
        raise BillingError("Only draft invoices can be confirmed.")
    inv.state = Invoice.CONFIRMED
    inv.save(update_fields=["state"])


@transaction.atomic
def cancel_invoice(inv):
    inv = Invoice.objects.get(pk=inv.pk)
    if inv.state not in (Invoice.DRAFT, Invoice.CONFIRMED) or inv.payments.exists():
        raise BillingError("Only unpaid draft or confirmed invoices can be cancelled.")
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
    if inv.state != Invoice.CANCELLED or hasattr(inv, "replacement"):
        raise BillingError("Only a cancelled invoice without a replacement can be replaced.")
    sub = inv.subscription
    idx = _period_index(sub, inv.period_start)
    return create_invoice_for_period(sub, idx, timezone.localdate(), replaces=inv)


def _period_index(sub, start):
    i = 0
    while add_interval(sub.start_date, sub.plan.interval, i) < start:
        i += 1
    return i


@transaction.atomic
def record_payment(inv, *, amount, method, date, note="", user=None):
    inv = Invoice.objects.get(pk=inv.pk)  # fresh row inside the (immediate) write transaction
    if inv.state != Invoice.CONFIRMED:
        raise BillingError("Payments can be recorded only against confirmed invoices.")
    amount = q(amount)
    if amount <= 0:
        raise BillingError("Amount must be positive.")
    if amount > inv.balance:
        raise BillingError(f"Amount exceeds the outstanding balance of {settings.CURRENCY_SYMBOL}{inv.balance}.")
    pay = Payment.objects.create(invoice=inv, amount=amount, method=method, date=date, note=note, recorded_by=user)
    inv.amount_paid += amount
    if inv.amount_paid >= inv.total:
        inv.state = Invoice.PAID
    inv.save(update_fields=["amount_paid", "state"])
    return pay


def send_invoice(inv, request=None):
    if inv.state == Invoice.CANCELLED:
        raise BillingError("A cancelled invoice cannot be sent.")
    if inv.state == Invoice.DRAFT:
        confirm_invoice(inv)
    link = ""
    if request is not None:
        from django.urls import reverse
        link = f"\nView and print it online: {request.build_absolute_uri(reverse('core:portal_invoice', args=[inv.pk]))}\n"
    from core.models import CompanySettings
    co = CompanySettings.get()
    pay = f"\nHow to pay:\n{co.bank_details}\n" if co.bank_details else ""
    body = (f"Hello {inv.bill_to_name},\n\nInvoice {inv.number} for {inv.period_start:%d %b %Y} – {inv.period_end:%d %b %Y} "
            f"is ready.\nTotal: {settings.CURRENCY_SYMBOL}{inv.total}   Due: {inv.due_date:%d %b %Y}\n{link}{pay}\nThank you,\n{co.name}")
    EmailMessage(f"Invoice {inv.number} from {co.name}", body, to=[inv.bill_to_email], reply_to=[co.email] if co.email else None).send(fail_silently=False)
    inv.sent_at = timezone.now()
    inv.save(update_fields=["sent_at"])


# ---------- collections ----------
def send_reminder(inv, user=None):
    """Dunning email for an overdue invoice. Increments the counter only if the mail was accepted."""
    if not inv.is_overdue:
        raise BillingError("Only overdue invoices can be chased.")
    from core import audit
    from core.models import CompanySettings
    co = CompanySettings.get()
    pay = f"\nHow to pay:\n{co.bank_details}\n" if co.bank_details else ""
    body = (f"Hello {inv.bill_to_name},\n\nInvoice {inv.number} was due on {inv.due_date:%d %b %Y} and {settings.CURRENCY_SYMBOL}{inv.balance} "
            f"is still outstanding ({inv.days_overdue} days overdue).\nIf you have already paid, please ignore this note.\n{pay}\nThank you,\n{co.name}")
    EmailMessage(f"Reminder: invoice {inv.number} is overdue", body, to=[inv.bill_to_email],
                 reply_to=[co.email] if co.email else None).send(fail_silently=False)
    inv.reminder_count += 1
    inv.last_reminded_at = timezone.now()
    inv.save(update_fields=["reminder_count", "last_reminded_at"])
    audit.log(user, "invoice.remind", inv, f"reminder #{inv.reminder_count}")


def send_due_reminders(as_of=None):
    """Chase overdue invoices not reminded within the configured repeat window; notify customers of upcoming expiry once."""
    from datetime import timedelta
    from core import audit
    from core.models import CompanySettings
    co = CompanySettings.get()
    now = timezone.now()
    today = as_of or timezone.localdate()
    sent = failed = 0
    overdue = Invoice.objects.filter(state=Invoice.CONFIRMED, due_date__lt=today, amount_paid__lt=F("total"))
    for inv in overdue.filter(Q(last_reminded_at__isnull=True) | Q(last_reminded_at__lt=now - timedelta(days=co.reminder_repeat_days))):
        try:
            send_reminder(inv)
            sent += 1
        except Exception:
            failed += 1
    notices = 0
    horizon = today + timedelta(days=co.reminder_days_before_renewal)
    for s in Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__gte=today, expiry_date__lte=horizon,
                                         renewal_notice_sent_at__isnull=True).select_related("customer", "plan"):
        try:
            EmailMessage(f"Your subscription {s.number} renews soon",
                         f"Hello {s.customer.name},\n\nYour {s.plan.name} subscription {s.number} expires on {s.expiry_date:%d %b %Y}. "
                         f"{'Sign in to your portal to renew it.' if s.plan.allow_renew else 'Please contact us to arrange the next term.'}\n\nThank you,\n{co.name}",
                         to=[s.customer.email]).send(fail_silently=False)
            s.renewal_notice_sent_at = now
            s.save(update_fields=["renewal_notice_sent_at"])
            notices += 1
        except Exception:
            failed += 1
    audit.log(None, "billing.reminders", detail=f"{sent} overdue reminders, {notices} renewal notices, {failed} failed")
    return sent, notices, failed


# ---------- mid-period seat changes ----------
@transaction.atomic
def create_adjustment_invoice(sub, before, today=None, user=None):
    """Draft invoice for the prorated increase of lines changed mid-period.

    `before` maps line pk -> pre-change pre-tax amount (new lines map to 0). Only increases are billed;
    reductions simply take effect from the next recurring invoice."""
    from datetime import timedelta
    today = today or timezone.localdate()
    if sub.state != Subscription.ACTIVE or sub.periods_billed == 0:
        return None
    idx = sub.periods_billed - 1  # the period already billed
    p_start = add_interval(sub.start_date, sub.plan.interval, idx)
    p_end = add_interval(sub.start_date, sub.plan.interval, idx + 1) - timedelta(days=1)
    if not (p_start <= today <= p_end):
        return None
    total_days = (p_end - p_start).days + 1
    left = (p_end - today).days + 1
    rows, subtotal, tax_total = [], ZERO, ZERO
    for l in sub.lines.select_related("product", "variant", "tax"):
        diff = l.amount - before.get(l.pk, ZERO)
        if diff <= 0:
            continue
        amt = q(diff * left / total_days)
        if amt <= 0:
            continue
        tax = q(amt * l.tax_rate / 100)
        rows.append(dict(description=f"{l.description} · prorated change {today:%d %b}–{p_end:%d %b}", quantity=1, unit_price=amt, discount=ZERO,
                         tax_name=l.tax.name if l.tax_id else "", tax_rate=l.tax_rate, tax_amount=tax, amount=amt))
        subtotal += amt
        tax_total += tax
    if not rows:
        return None
    c = sub.customer
    inv = Invoice.objects.create(
        subscription=sub, customer=c, kind=Invoice.ADJUSTMENT, period_start=today, period_end=p_end, issue_date=today,
        due_date=today + timedelta(days=sub.payment_term_days), bill_to_name=c.display_name, bill_to_email=c.email,
        bill_to_address=c.address, bill_to_tax_id=c.tax_id, subtotal=subtotal, tax_total=tax_total, total=subtotal + tax_total)
    InvoiceLine.objects.bulk_create([InvoiceLine(invoice=inv, **r) for r in rows])
    from core import audit
    audit.log(user, "invoice.adjustment", inv, f"prorated change on {sub.number}")
    return inv


# ---------- customer checkout (test mode) ----------
def pay_online(inv, user=None):
    """Simulated checkout for the portal: pays the full balance. No card data is collected or stored."""
    from core.models import CompanySettings
    if not CompanySettings.get().online_payments:
        raise BillingError("Online payment is not enabled.")
    pay = record_payment(inv, amount=Invoice.objects.get(pk=inv.pk).balance, method="online", date=timezone.localdate(),
                         note="Online checkout (test mode)", user=user)
    from core import audit
    audit.log(user, "payment.online", inv, f"{pay.amount} (test mode)")
    return pay


@transaction.atomic
def refund_payment(inv, *, amount, method, date, note="", user=None):
    """Money returned to the customer. Stored as a negative payment so cash totals stay honest;
    a refund that leaves a balance reopens a paid invoice."""
    inv = Invoice.objects.get(pk=inv.pk)
    amount = q(amount)
    if inv.state not in (Invoice.CONFIRMED, Invoice.PAID):
        raise BillingError("Only confirmed or paid invoices can be refunded.")
    if amount <= 0:
        raise BillingError("Refund amount must be positive.")
    if amount > inv.amount_paid:
        raise BillingError(f"Cannot refund more than was paid ({settings.CURRENCY_SYMBOL}{inv.amount_paid}).")
    Payment.objects.create(invoice=inv, amount=-amount, method=method, date=date, note=(f"Refund. {note}").strip(), recorded_by=user)
    inv.amount_paid -= amount
    inv.state = Invoice.PAID if inv.amount_paid >= inv.total else Invoice.CONFIRMED
    inv.save(update_fields=["amount_paid", "state"])
    from core import audit
    audit.log(user, "payment.refund", inv, f"{amount} via {method}")
