"""Payments: recording, customer-reported (pending) payments, failures, refunds. All balance maths happens here."""
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from core import audit, automation, reports_cache
from core.money import q

from .documents import BillingError
from .models import Invoice, Payment


def _lock(pk):
    qs = Invoice.objects.select_related("subscription__plan")
    if connection.features.has_select_for_update:
        options = {"of": ("self",)} if connection.features.has_select_for_update_of else {}
        qs = qs.select_for_update(**options)
    return qs.get(pk=pk)


def _lock_subscription_for_invoice(invoice_id):
    # Match amendment transactions: subscription first, then invoice.
    subscription_id = Invoice.objects.values_list("subscription_id", flat=True).get(pk=invoice_id)
    from subscriptions.models import Subscription
    qs = Subscription.objects
    if connection.features.has_select_for_update:
        qs = qs.select_for_update()
    return qs.get(pk=subscription_id)


def _prior_payment(invoice_id, *, idempotency_key=None, provider="", provider_ref=""):
    prior = Payment.objects.filter(idempotency_key=idempotency_key).first() if idempotency_key else None
    if prior is None and provider and provider_ref:
        prior = Payment.objects.filter(provider=provider, provider_ref=provider_ref).first()
    if prior and prior.invoice_id != invoice_id:
        raise BillingError("This payment reference was already used for another invoice.")
    return prior


def _after_payment(inv):
    """Invoice just changed balance: maybe activate a pay-to-activate subscription, clear payment exception."""
    from subscriptions import services as subs
    from subscriptions.models import Subscription
    sub = inv.subscription
    if inv.state == Invoice.PAID and sub.state == Subscription.CONFIRMED and sub.plan.activation == "payment":
        subs.activate(Subscription.objects.select_related("plan", "customer").get(pk=sub.pk))
    if sub.payment_exception and not Invoice.objects.filter(subscription=sub, state=Invoice.CONFIRMED, due_date__lt=timezone.localdate()).exclude(kind="credit_note").exists():
        Subscription.objects.filter(pk=sub.pk).update(payment_exception=False)
    reports_cache.invalidate()


@transaction.atomic
def record_payment(inv, *, amount, method="bank", date=None, note="", reference="", user=None, provider="", provider_ref="",
                   is_test=False, idempotency_key=None):
    """Apply money to an invoice. Idempotent on `idempotency_key` and on (provider, provider_ref)."""
    amount = q(amount)
    _lock_subscription_for_invoice(inv.pk)
    inv = _lock(inv.pk)
    prior = _prior_payment(inv.pk, idempotency_key=idempotency_key, provider=provider, provider_ref=provider_ref)
    if prior:
        if prior.status != Payment.SUCCEEDED or prior.amount != amount or prior.is_test != is_test:
            raise BillingError("This payment reference is already recorded with a different amount, mode, or status.")
        return prior
    if inv.kind == Invoice.CREDIT_NOTE or inv.state != Invoice.CONFIRMED:
        raise BillingError("Payments can be recorded only against confirmed invoices.")
    if amount <= 0:
        raise BillingError("Amount must be positive.")
    if amount > inv.balance:
        raise BillingError(f"Amount exceeds the outstanding balance of {settings.CURRENCY_SYMBOL}{inv.balance}.")
    try:
        with transaction.atomic():
            pay = Payment.objects.create(invoice=inv, amount=amount, method=method, date=date or timezone.localdate(), note=note, reference=reference,
                                         provider=provider, provider_ref=provider_ref, is_test=is_test, recorded_by=user if getattr(user, "pk", None) else None,
                                         idempotency_key=idempotency_key or None)
    except IntegrityError:
        prior = _prior_payment(inv.pk, idempotency_key=idempotency_key, provider=provider, provider_ref=provider_ref)
        if prior:
            if prior.status != Payment.SUCCEEDED or prior.amount != amount or prior.is_test != is_test:
                raise BillingError("This payment reference is already recorded with a different amount, mode, or status.")
            return prior
        raise
    inv.amount_paid += amount
    if inv.balance <= 0:
        inv.state = Invoice.PAID
    inv.save(update_fields=["amount_paid", "state"])
    _after_payment(inv)
    audit.log(user, "payment.record", inv, f"{amount} via {method}" + (" (test)" if is_test else ""))
    return pay


@transaction.atomic
def report_payment(inv, *, amount, method, reference, date=None, note="", user=None):
    """Customer says they paid by bank/UPI. Nothing is applied until staff confirm it against the statement."""
    inv = _lock(inv.pk)
    if inv.state != Invoice.CONFIRMED or inv.kind == Invoice.CREDIT_NOTE:
        raise BillingError("This invoice isn't open for payment.")
    amount = q(amount)
    if amount <= 0 or amount > inv.balance:
        raise BillingError(f"Enter an amount up to {settings.CURRENCY_SYMBOL}{inv.balance}.")
    reference = reference.strip()[:120]
    if not reference:
        raise BillingError("Add your transaction reference so we can match it.")
    prior = Payment.objects.filter(invoice=inv, reference=reference).first()
    if prior:
        if prior.status == Payment.PENDING and prior.amount == amount and prior.method == method:
            return prior
        raise BillingError("This transaction reference has already been reported.")
    pay = Payment.objects.create(invoice=inv, status=Payment.PENDING, amount=amount, method=method, date=date or timezone.localdate(),
                                 reference=reference, note=note[:240], reconciled=False, recorded_by=user if getattr(user, "pk", None) else None)
    audit.log(user, "payment.reported", inv, f"{amount} ref {reference}")
    from core.models import Notification
    Notification.objects.create(text=f"{inv.bill_to_name} reported a payment of {settings.CURRENCY_SYMBOL}{amount} for {inv.number}", url=f"/billing/invoices/{inv.pk}/")
    return pay


@transaction.atomic
def confirm_pending(pay, *, user=None):
    pay = Payment.objects.select_for_update().get(pk=pay.pk) if connection.features.has_select_for_update else Payment.objects.get(pk=pay.pk)
    if pay.status == Payment.SUCCEEDED and pay.reconciled:
        return pay
    if pay.status != Payment.PENDING:
        raise BillingError("This payment isn't awaiting confirmation.")
    _lock_subscription_for_invoice(pay.invoice_id)
    inv = _lock(pay.invoice_id)
    if inv.state != Invoice.CONFIRMED or pay.amount > inv.balance:
        raise BillingError("The invoice balance has changed; reject this report or adjust it.")
    pay.status, pay.reconciled = Payment.SUCCEEDED, True
    pay.save(update_fields=["status", "reconciled"])
    inv.amount_paid += pay.amount
    if inv.balance <= 0:
        inv.state = Invoice.PAID
    inv.save(update_fields=["amount_paid", "state"])
    _after_payment(inv)
    audit.log(user, "payment.reconcile", inv, f"{pay.amount} ref {pay.reference}")
    return pay


@transaction.atomic
def reject_pending(pay, *, reason="", user=None):
    pay = Payment.objects.select_for_update().get(pk=pay.pk) if connection.features.has_select_for_update else Payment.objects.get(pk=pay.pk)
    if pay.status != Payment.PENDING:
        raise BillingError("This payment isn't awaiting confirmation.")
    pay.status, pay.failure_reason, pay.reconciled = Payment.FAILED, reason[:240] or "Not found on statement", True
    pay.save(update_fields=["status", "failure_reason", "reconciled"])
    audit.log(user, "payment.reject", pay.invoice, pay.failure_reason)


@transaction.atomic
def fail_payment(inv, *, amount, method, reason, provider="", provider_ref="", is_test=False):
    """A payment attempt failed at the provider. Records it, flags the subscription and fires automation. Idempotent."""
    sub = _lock_subscription_for_invoice(inv.pk)
    inv = _lock(inv.pk)
    if provider and provider_ref:
        prior = Payment.objects.filter(provider=provider, provider_ref=provider_ref).first()
        if prior:
            if prior.invoice_id != inv.pk:
                raise BillingError("This payment reference was already used for another invoice.")
            return None
    try:
        with transaction.atomic():
            pay = Payment.objects.create(invoice=inv, status=Payment.FAILED, amount=q(amount), method=method, provider=provider if provider_ref else "", provider_ref=provider_ref,
                                         is_test=is_test, failure_reason=reason[:240], note="Payment failed")
    except IntegrityError:
        if provider and provider_ref:
            prior = Payment.objects.filter(provider=provider, provider_ref=provider_ref).first()
            if prior:
                if prior.invoice_id != inv.pk:
                    raise BillingError("This payment reference was already used for another invoice.")
                return None
        raise
    if not sub.payment_exception:
        sub.payment_exception = True
        sub.save(update_fields=["payment_exception"])
    automation.fire("payment_failed", inv, bucket=f"p{pay.pk}")
    audit.log(None, "payment.failed", inv, reason)
    reports_cache.invalidate()
    return pay


@transaction.atomic
def refund_payment(inv, *, amount, method, date=None, note="", user=None, idempotency_key=None):
    """Money returned to the customer, stored as a negative payment so cash totals stay honest."""
    inv = _lock(inv.pk)
    amount = q(amount)
    prior = Payment.objects.filter(idempotency_key=idempotency_key).first() if idempotency_key else None
    if prior:
        if prior.invoice_id != inv.pk:
            raise BillingError("This refund reference was already used for another invoice.")
        if prior.amount != -amount or prior.status != Payment.SUCCEEDED:
            raise BillingError("This refund reference was already used for a different refund.")
        return prior
    if inv.state not in (Invoice.CONFIRMED, Invoice.PAID) or inv.kind == Invoice.CREDIT_NOTE:
        raise BillingError("Only confirmed or paid invoices can be refunded.")
    if amount <= 0:
        raise BillingError("Refund amount must be positive.")
    if amount > inv.amount_paid:
        raise BillingError(f"Cannot refund more than was paid ({settings.CURRENCY_SYMBOL}{inv.amount_paid}).")
    receipts = inv.payments.filter(status=Payment.SUCCEEDED)
    live_paid = receipts.filter(is_test=False).aggregate(total=Sum("amount"))["total"] or 0
    test_paid = receipts.filter(is_test=True).aggregate(total=Sum("amount"))["total"] or 0
    if live_paid > 0 and test_paid > 0 and amount > live_paid:
        raise BillingError("This invoice mixes test and live payments; reconcile the refund against the original payment first.")
    is_test = live_paid <= 0 and test_paid > 0
    try:
        with transaction.atomic():
            pay = Payment.objects.create(invoice=inv, amount=-amount, method=method, date=date or timezone.localdate(), note=(f"Refund. {note}").strip(),
                                         recorded_by=user if getattr(user, "pk", None) else None, is_test=is_test,
                                         idempotency_key=idempotency_key or None)
    except IntegrityError:
        prior = Payment.objects.filter(idempotency_key=idempotency_key).first() if idempotency_key else None
        if prior:
            if prior.invoice_id == inv.pk and prior.amount == -amount and prior.status == Payment.SUCCEEDED:
                return prior
            raise BillingError("This refund reference was already used for a different refund.")
        raise
    inv.amount_paid -= amount
    inv.state = Invoice.PAID if inv.balance <= 0 else Invoice.CONFIRMED
    inv.save(update_fields=["amount_paid", "state"])
    audit.log(user, "payment.refund", inv, f"{amount} via {method}")
    reports_cache.invalidate()
    return pay
