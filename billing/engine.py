"""The recurring billing engine. Used by the web UI and the `run_billing` command so they cannot diverge.

Per subscription and period, in one transaction: lock the row, apply scheduled amendments, create the BillingPeriod
(unique per subscription+index), create the invoice snapshot, claim discount use, advance the schedule. A crash at any
point rolls the whole unit back; a re-run bills exactly the periods that are still missing."""
import traceback
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from core import audit, reports_cache
from core.models import CompanySettings
from subscriptions import services as subs
from subscriptions.models import Subscription, add_interval

from .documents import confirm_invoice, create_invoice_for_period, send_invoice
from .models import BillingFailure, BillingPeriod, BillingRun, Invoice


def _lock_sub(sid):
    qs = Subscription.objects.select_related("plan", "customer")
    if connection.features.has_select_for_update:
        options = {}
        if connection.features.has_select_for_update_skip_locked:
            options["skip_locked"] = True  # another runner owns it: skip, don't wait
        if connection.features.has_select_for_update_of:
            options["of"] = ("self",)
        qs = qs.select_for_update(**options)
    return qs.filter(pk=sid).first()


def run_billing(as_of=None, trigger="scheduler", only=None):
    as_of = as_of or timezone.localdate()
    run = BillingRun.objects.create(as_of=as_of, trigger=trigger)
    errors, new_invoices = [], []
    qs = Subscription.objects.filter(state=Subscription.ACTIVE, next_invoice_date__lte=as_of)
    if only:
        qs = qs.filter(pk__in=only)
    for sid in list(qs.order_by("id").values_list("id", flat=True)):
        while True:
            try:
                with transaction.atomic():
                    sub = _lock_sub(sid)
                    if sub is None:
                        break
                    due = sub.next_invoice_date
                    if sub.state != Subscription.ACTIVE or not due or due > as_of or (sub.expiry_date and due > sub.expiry_date):
                        break
                    if sub.pending_cancel_at and due > sub.pending_cancel_at:
                        break
                    subs.apply_scheduled(sub, as_of)
                    sub = _lock_sub(sid) or sub
                    idx = sub.periods_billed
                    existing = BillingPeriod.objects.filter(subscription=sub, index=idx, invoice__isnull=False).first()
                    if existing:
                        run.skipped += 1  # already billed (an earlier run died after the invoice, before the schedule moved)
                    else:
                        new_invoices.append(create_invoice_for_period(sub, idx, as_of).pk)
                        run.created += 1
                    sub.periods_billed = idx + 1
                    sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
                    sub.save(update_fields=["periods_billed", "next_invoice_date"])
                BillingFailure.objects.filter(subscription_id=sid, resolved_at__isnull=True).update(resolved_at=timezone.now())
            except Exception:  # one bad subscription must not stop the run; it is retried next time
                run.failed += 1
                msg = traceback.format_exc(limit=2)
                errors.append(f"{sid}: {msg}")
                BillingFailure.objects.create(subscription_id=sid, run=run, message=msg.strip().splitlines()[-1][:500])
                break
    co = CompanySettings.get()
    if new_invoices and (co.auto_confirm_invoices or co.auto_email_invoices):
        for inv in Invoice.objects.filter(pk__in=new_invoices):
            try:
                send_invoice(inv) if co.auto_email_invoices else confirm_invoice(inv)
            except Exception:
                run.failed += 1
                errors.append(f"{inv.number}: {traceback.format_exc(limit=1)}")
    run.closed, run.renewed = maintenance(as_of)
    audit.log(None, "billing.run", detail=f"{trigger}: created {run.created}, skipped {run.skipped}, failed {run.failed}, closed {run.closed}, renewed {run.renewed}")
    run.errors = "\n".join(errors)[:5000]
    run.finished_at = timezone.now()
    run.save()
    reports_cache.invalidate()
    return run


def maintenance(as_of):
    """After billing: scheduled cancellations, term ends (auto-renew or close), expired quotations, payment-exception flags."""
    closed = renewed = 0
    for sub in Subscription.objects.filter(state__in=[Subscription.ACTIVE, Subscription.PAUSED], pending_cancel_at__lt=as_of).select_related("plan", "customer"):
        closed += bool(subs._finish_close(sub, effective=sub.pending_cancel_at + timedelta(days=1)))
    ended = Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__lt=as_of).select_related("plan", "customer")
    for sub in ended:
        if sub.next_invoice_date and sub.next_invoice_date <= sub.expiry_date:
            continue  # periods inside the term still to bill
        has_renewal = sub.renewals.exclude(state=Subscription.CANCELLED).exists()
        if not has_renewal and sub.renewal_policy == "auto" and sub.plan.allow_renew:
            subs.renew(sub, accept=True, auto=True)
            has_renewal = True
            renewed += 1
        if has_renewal:
            sub.close_reason = "expired"
            closed += bool(subs._finish_close(sub, effective=sub.expiry_date + timedelta(days=1), movement="renewal"))
        elif sub.plan.auto_close:
            sub.close_reason = "expired"
            closed += bool(subs._finish_close(sub, effective=sub.expiry_date + timedelta(days=1)))
    # activate renewal terms whose start date has arrived is handled by billing itself (next_invoice_date = start_date)
    for q_ in Subscription.objects.filter(state__in=[Subscription.DRAFT, Subscription.QUOTATION], quote_valid_until__lt=as_of):
        q_.state = Subscription.CANCELLED
        q_.close_note = "Quotation expired"
        q_.save(update_fields=["state", "close_note"])
        subs.record_change(q_, "rejected", detail="Quotation expired")
    co = CompanySettings.get()
    cutoff = as_of - timedelta(days=co.payment_exception_days)
    late = Invoice.objects.filter(state=Invoice.CONFIRMED, due_date__lt=cutoff).exclude(kind="credit_note").values_list("subscription_id", flat=True).distinct()
    Subscription.objects.filter(pk__in=list(late), payment_exception=False).update(payment_exception=True)
    return closed, renewed


def retry_failures(user=None):
    """Re-run billing only for subscriptions with an unresolved failure."""
    ids = list(BillingFailure.objects.filter(resolved_at__isnull=True).values_list("subscription_id", flat=True).distinct())
    return run_billing(trigger=f"retry:{getattr(user, 'email', 'system')}", only=ids) if ids else None
