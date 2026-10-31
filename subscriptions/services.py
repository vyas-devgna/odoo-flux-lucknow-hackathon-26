"""Subscription lifecycle, contract changes and proration. Every transition is enforced here, on the server,
for staff and portal alike, and every change is recorded as an effective-dated SubscriptionChange."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan, Product, Tax
from core import audit, automation, mail, reports_cache
from core.models import CompanySettings
from core.money import ZERO, q

from .models import (QuoteAcceptance, ScheduledChange, Subscription, SubscriptionChange, SubscriptionLine, add_interval)


class TransitionError(Exception):
    pass


def _require(sub, action):
    if not sub.can(action):
        raise TransitionError(f"Cannot {action} a {sub.get_state_display().lower()} subscription on plan “{sub.plan.name}”.")


def fresh(sub):
    return Subscription.objects.select_related("plan", "customer").get(pk=sub.pk)


# ------------------------------------------------------------------ change history / MRR movements
def mrr_of(sub):
    return Subscription.objects.get(pk=sub.pk).recurring_monthly if sub.pk else ZERO


def record_change(sub, kind, *, before=None, after=None, effective=None, detail="", actor=None, movement=None):
    """Append an effective-dated change. `before`/`after` are MRR values; the movement label is derived unless given."""
    before = ZERO if before is None else before
    after = ZERO if after is None else after
    if movement is None:
        if kind == "activated":
            movement = SubscriptionChange.NEW
        elif kind == "closed":
            movement = SubscriptionChange.CHURN
        elif kind == "paused":
            movement = SubscriptionChange.PAUSE
        elif kind == "resumed":
            movement = SubscriptionChange.REACTIVATION
        elif after > before:
            movement = SubscriptionChange.EXPANSION
        elif after < before:
            movement = SubscriptionChange.CONTRACTION
        else:
            movement = SubscriptionChange.NONE
    ch = SubscriptionChange.objects.create(subscription=sub, kind=kind, effective_date=effective or timezone.localdate(),
                                           mrr_before=before, mrr_after=after, movement=movement, detail=detail[:300], actor=actor if getattr(actor, "pk", None) else None)
    reports_cache.invalidate()
    return ch


# ------------------------------------------------------------------ creation
def _default_tax(day=None, product=None):
    if product is not None and product.tax_id and product.tax.applies_on(day or timezone.localdate()):
        return product.tax
    return Tax.default(day)


def create_subscription(*, customer, plan, template=None, start_date=None, expiry_date=None, payment_terms=None,
                        notes="", user=None, quantity=None, salesperson=None, validity_days=None):
    start_date = start_date or timezone.localdate()
    validity = validity_days or (template.validity_days if template else 30)
    qty = max(quantity or plan.min_quantity, plan.min_quantity)
    terms = payment_terms or plan.payment_terms or CompanySettings.get().default_payment_terms
    with transaction.atomic():
        sub = Subscription.objects.create(
            customer=customer, plan=plan, template=template, start_date=start_date, payment_terms=terms,
            renewal_policy=plan.renewal_policy, salesperson=salesperson or (user if getattr(user, "pk", None) and user.is_internal else None),
            expiry_date=expiry_date or add_interval(start_date, Plan.YEARLY, 1) - timedelta(days=1), notes=notes,
            quote_valid_until=timezone.localdate() + timedelta(days=validity), created_by=user if getattr(user, "pk", None) else None)
        # Plan price pre-fills one base line; products are priced from the catalogue. The plan is not charged twice.
        SubscriptionLine.objects.create(subscription=sub, label=plan.name, quantity=qty, unit_price=plan.unit_price_for(qty),
                                        tax=Tax.default(start_date), is_plan_line=True)
        if template:
            for tl in template.lines.select_related("product", "variant"):
                SubscriptionLine.objects.create(subscription=sub, product=tl.product, variant=tl.variant, quantity=max(tl.quantity, 1),
                                                unit_price=tl.unit_price, tax=_default_tax(start_date, tl.product), recurring=tl.product.recurring)
        record_change(sub, "created", effective=timezone.localdate(), detail=f"{plan.name} ×{qty}", actor=user)
    return sub


def clone_as_quote(sub, *, user=None, kind="alternative"):
    """Alternative / upsell quotation: a fresh draft with the same lines, linked to the original."""
    with transaction.atomic():
        new = Subscription.objects.create(
            customer=sub.customer, plan=sub.plan, template=sub.template, state=Subscription.DRAFT, start_date=max(sub.start_date, timezone.localdate()),
            expiry_date=sub.expiry_date, payment_terms=sub.payment_terms, renewal_policy=sub.renewal_policy, salesperson=sub.salesperson,
            alternative_of=sub, quote_valid_until=timezone.localdate() + timedelta(days=30), created_by=user if getattr(user, "pk", None) else None,
            notes=f"{kind.title()} quotation based on {sub.number}")
        for l in sub.lines.all():
            SubscriptionLine.objects.create(subscription=new, product=l.product, variant=l.variant, label=l.label, quantity=l.quantity,
                                            unit_price=l.unit_price, tax=l.tax, is_plan_line=l.is_plan_line, recurring=l.recurring)
        record_change(new, "created", detail=f"{kind} of {sub.number}", actor=user)
    return new


# ------------------------------------------------------------------ quotation workflow
def send_quotation(sub, actor=None):
    _require(sub, "send")
    if not sub.lines.exists():
        raise TransitionError("Add at least one line first.")
    sub.state = Subscription.QUOTATION
    sub.save(update_fields=["state"])
    email_quotation(sub)


def email_quotation(sub):
    co = CompanySettings.get()
    registered = bool(getattr(sub.customer, "user_id", None))
    link = settings.SITE_URL + (reverse("core:portal_subscription", args=[sub.pk]) if registered else reverse("accounts:signup"))
    lines = "\n".join(f"  - {l.description} x {l.quantity} @ {settings.CURRENCY_SYMBOL}{l.unit_price}" for l in sub.lines.all())
    body = (f"Hello {sub.customer.name},\n\nHere is your quotation {sub.number} for the {sub.plan.name} plan "
            f"({sub.plan.get_interval_display().lower()} billing), valid until {sub.quote_valid_until:%d %b %Y}.\n\n{lines}\n\n"
            f"Total per period: {settings.CURRENCY_SYMBOL}{sub.total} (incl. tax)\n\nReview and accept it online: {link}\n"
            + ("" if registered else f"(Create your account with {sub.customer.email} to see it.)\n") + f"\nThank you,\n{co.name}")
    return mail.queue_email(sub.customer.email, f"Quotation {sub.number} from {co.name}", body, dedupe_key=f"quote:{sub.pk}:{sub.revision}", related=sub)


def revise_quotation(sub, actor=None):
    """Reopen a sent quotation for editing. Bumps the revision so the previous version's acceptance can't be reused."""
    if sub.state != Subscription.QUOTATION:
        raise TransitionError("Only a sent quotation can be revised.")
    sub.state = Subscription.DRAFT
    sub.revision += 1
    sub.save(update_fields=["state", "revision"])


def _record_acceptance(sub, actor, signed_name, ip, how):
    return QuoteAcceptance.objects.create(
        subscription=sub, revision=sub.revision, accepted_by=actor if getattr(actor, "pk", None) else None, signed_name=signed_name[:160],
        ip=ip or None, total_snapshot=sub.total, terms_snapshot=f"{how}; {sub.plan.name}; {sub.get_payment_terms_display()}; starts {sub.start_date:%d %b %Y}")


def accept(sub, *, actor=None, signed_name="", ip=None, how="Accepted online"):
    """Customer (or staff on their behalf) accepts the quotation. Cancels sibling alternatives and, depending on the
    plan, activates straight away or waits for the first payment."""
    _require(sub, "confirm")
    if sub.quote_expired:
        raise TransitionError("This quotation has expired. Extend its validity date first.")
    if not sub.lines.exists():
        raise TransitionError("Add at least one line first.")
    with transaction.atomic():
        sub.state = Subscription.CONFIRMED
        sub.save(update_fields=["state"])
        _record_acceptance(sub, actor, signed_name, ip, how)
        record_change(sub, "accepted", detail=how, actor=actor)
        root = sub.alternative_of or sub
        siblings = Subscription.objects.filter(state__in=[Subscription.DRAFT, Subscription.QUOTATION]).exclude(pk=sub.pk).filter(
            pk__in=[root.pk] + list(root.alternatives.values_list("pk", flat=True))).filter(customer=sub.customer)
        for sib in siblings:
            sib.state = Subscription.CANCELLED
            sib.save(update_fields=["state"])
            record_change(sib, "rejected", detail=f"superseded by {sub.number}")
    if sub.plan.activation == Plan.ACT_ACCEPT:
        activate(fresh(sub), actor=actor)
    elif sub.plan.activation == Plan.ACT_PAYMENT:
        from billing import services as billing
        billing.create_first_invoice(fresh(sub), actor=actor)
    return fresh(sub)


def confirm(sub, actor=None):
    """Staff records that the customer accepted (verbal/email). Same rules as online acceptance."""
    return accept(sub, actor=actor, signed_name=getattr(actor, "name", "") or "", how="Confirmed by staff")


def reject(sub, *, actor=None, reason=""):
    _require(sub, "reject")
    sub.state = Subscription.CANCELLED
    sub.close_note = reason[:300]
    sub.save(update_fields=["state", "close_note"])
    record_change(sub, "rejected", detail=reason, actor=actor)


# ------------------------------------------------------------------ contract lifecycle
def activate(sub, *, effective=None, actor=None):
    _require(sub, "activate")
    effective = effective or sub.start_date
    with transaction.atomic():
        sub.state = Subscription.ACTIVE
        sub.activated_at = timezone.now()
        sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
        sub.save(update_fields=["state", "activated_at", "next_invoice_date"])
        record_change(sub, "activated", before=ZERO, after=mrr_of(sub), effective=effective, actor=actor)
    automation.fire("subscription_activated", sub)
    return sub


def pause(sub, actor=None):
    _require(sub, "pause")
    before = mrr_of(sub)
    sub.state = Subscription.PAUSED
    sub.save(update_fields=["state"])
    record_change(sub, "paused", before=before, after=ZERO, actor=actor)
    automation.fire("subscription_paused", sub)


def resume(sub, actor=None):
    """Billing continues from the next unbilled period; paused time is not back-billed."""
    _require(sub, "resume")
    today = timezone.localdate()
    while add_interval(sub.start_date, sub.plan.interval, sub.periods_billed) < today:
        sub.periods_billed += 1
    sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
    sub.state = Subscription.ACTIVE
    sub.save(update_fields=["state", "periods_billed", "next_invoice_date"])
    record_change(sub, "resumed", before=ZERO, after=mrr_of(sub), actor=actor)


def close(sub, *, actor=None, reason="customer_request", note="", immediate=None, by_customer=False):
    """Close early. Honours the plan's minimum term and cancellation timing; scheduled cancellations are closed by the billing run."""
    _require(sub, "close")
    if sub.in_minimum_term and by_customer:
        raise TransitionError(f"This plan has a minimum term of {sub.plan.min_duration_periods} billing periods. Please contact us to end it earlier.")
    if by_customer and not sub.plan.self_serve_cancel:
        raise TransitionError("This plan can't be cancelled online. Please contact us.")
    if immediate is None:
        immediate = sub.plan.cancel_policy == Plan.CANCEL_ANYTIME or sub.periods_billed == 0
    sub.close_reason, sub.close_note = reason, note[:300]
    if not immediate:
        end = sub.next_invoice_date - timedelta(days=1)
        sub.pending_cancel_at = end
        sub.save(update_fields=["pending_cancel_at", "close_reason", "close_note"])
        record_change(sub, "cancel_scheduled", before=mrr_of(sub), after=mrr_of(sub), effective=end, detail=f"{reason}: ends {end:%d %b %Y}", actor=actor, movement=SubscriptionChange.NONE)
        return sub
    _finish_close(sub, actor=actor)
    return sub


def keep(sub, actor=None):
    """Withdraw a scheduled cancellation."""
    if not sub.pending_cancel_at:
        raise TransitionError("No cancellation is scheduled.")
    sub.pending_cancel_at = None
    sub.close_reason = sub.close_note = ""
    sub.save(update_fields=["pending_cancel_at", "close_reason", "close_note"])
    record_change(sub, "terms", detail="Scheduled cancellation withdrawn", actor=actor)


def _finish_close(sub, *, actor=None, effective=None, movement=None):
    before = mrr_of(sub) if sub.state == Subscription.ACTIVE else ZERO
    sub.state = Subscription.CLOSED
    sub.closed_at = timezone.now()
    sub.next_invoice_date = None
    sub.pending_cancel_at = None
    sub.save(update_fields=["state", "closed_at", "next_invoice_date", "pending_cancel_at"])
    record_change(sub, "closed", before=before, after=ZERO, effective=effective, detail=sub.get_close_reason_display() or "", actor=actor, movement=movement)
    automation.fire("subscription_closed", sub)


def renew(sub, *, actor=None, accept=False, auto=False):
    """Renewal creates a new linked term; the old record is never reopened.
    accept=False -> a renewal quotation to send; accept=True -> the new term is confirmed and starts at the old expiry."""
    _require(sub, "renew")
    with transaction.atomic():
        start = (sub.expiry_date + timedelta(days=1)) if sub.expiry_date else timezone.localdate()
        new = Subscription.objects.create(
            customer=sub.customer, plan=sub.plan, template=sub.template, state=Subscription.QUOTATION if not accept else Subscription.CONFIRMED,
            start_date=start, expiry_date=add_interval(start, Plan.YEARLY, 1) - timedelta(days=1), payment_terms=sub.payment_terms,
            renewal_policy=sub.renewal_policy, salesperson=sub.salesperson, renewed_from=sub, created_by=actor if getattr(actor, "pk", None) else sub.created_by,
            quote_valid_until=(sub.expiry_date or timezone.localdate()), notes=f"Renewal of {sub.number}")
        for l in sub.lines.all():
            SubscriptionLine.objects.create(subscription=new, product=l.product, variant=l.variant, label=l.label, quantity=l.quantity,
                                            unit_price=l.unit_price, tax=l.tax, is_plan_line=l.is_plan_line, recurring=l.recurring)
        record_change(new, "created", detail=f"renewal of {sub.number}", actor=actor)
        if accept:
            _record_acceptance(new, actor, "auto-renewal" if auto else (getattr(actor, "name", "") or ""), None, "Auto-renewed" if auto else "Renewed")
            new.state = Subscription.CONFIRMED
            new.save(update_fields=["state"])
            activate_renewal(new, sub, actor)
    return new


def activate_renewal(new, old, actor=None):
    """Activate the new term at its start date; MRR continuity is preserved with a zero-sum 'renewal' movement pair."""
    new.state = Subscription.ACTIVE
    new.activated_at = timezone.now()
    new.next_invoice_date = add_interval(new.start_date, new.plan.interval, 0)
    new.save(update_fields=["state", "activated_at", "next_invoice_date"])
    record_change(new, "renewed", before=ZERO, after=mrr_of(new), effective=new.start_date, detail=f"from {old.number}", actor=actor, movement="renewal")
    automation.fire("subscription_activated", new)


# ------------------------------------------------------------------ amendments: preview, apply, schedule
def _plan_line(sub):
    return next((l for l in sub.lines.all() if l.is_plan_line), None)


def _period(sub, today=None):
    """The billing period currently paid for, or None if nothing has been billed / it has ended."""
    today = today or timezone.localdate()
    if sub.state != Subscription.ACTIVE or not sub.periods_billed:
        return None
    idx = sub.periods_billed - 1
    start = add_interval(sub.start_date, sub.plan.interval, idx)
    end = add_interval(sub.start_date, sub.plan.interval, idx + 1) - timedelta(days=1)
    return (idx, start, end) if start <= today <= end else None


def _target(sub, plan=None, quantity=None, addons=None):
    """Compute the post-change lines without touching the database.
    Returns (keep_updates, additions, removals, notes) where each entry carries the unsaved SubscriptionLine."""
    plan = plan or sub.plan
    notes, keep, adds, removes = [], [], [], []
    if plan.pk != sub.plan_id:
        if not plan.is_available():
            raise TransitionError(f"“{plan.name}” isn't available.")
        if plan.interval != sub.plan.interval:
            raise TransitionError("Switching between billing frequencies isn't supported on a live contract. Create a new subscription instead.")
    pl = _plan_line(sub)
    qty = quantity if quantity is not None else (pl.quantity if pl else 1)
    if qty < plan.min_quantity:
        raise TransitionError(f"“{plan.name}” has a minimum of {plan.min_quantity}.")
    offered = set(plan.products.values_list("pk", flat=True))
    addons = addons or {}
    for l in sub.lines.select_related("product", "variant", "tax"):
        if l.is_plan_line:
            new = SubscriptionLine(subscription=sub, label=plan.name, quantity=qty, unit_price=plan.unit_price_for(qty), tax=l.tax,
                                   is_plan_line=True, recurring=True)
            keep.append((l, new))
        elif l.is_addon and (addons.get(l.product_id) is False or (plan.pk != sub.plan_id and l.product_id not in offered)):
            removes.append(l)
            if addons.get(l.product_id) is not False:
                notes.append(f"{l.product.name} isn't offered on {plan.name} and will be removed.")
        else:
            keep.append((l, l))
    have = {l.product_id for l in sub.lines.all() if l.product_id}
    for pid, on in addons.items():
        if on and pid not in have:
            prod = Product.objects.filter(pk=pid, type=Product.ADDON, active=True).first()
            if not prod or prod.pk not in offered:
                raise TransitionError("That add-on is not offered on this plan.")
            adds.append(SubscriptionLine(subscription=sub, product=prod, quantity=1, unit_price=prod.sales_price, tax=_default_tax(None, prod), recurring=prod.recurring))
    return plan, qty, keep, adds, removes, notes


def preview_change(sub, *, plan=None, quantity=None, addons=None, effective="now", today=None):
    """Everything the customer or staff member needs to see before committing: old vs new recurring charge,
    the prorated adjustment for the rest of the paid period, the amount due now and the next billing amount."""
    today = today or timezone.localdate()
    sub = fresh(sub)
    plan, qty, keep, adds, removes, notes = _target(sub, plan, quantity, addons)
    cur = [l for l in sub.lines.all()]
    before_net = sum((l.net_amount for l in cur), ZERO)
    before_total = sum((l.net_amount + l.tax_amount for l in cur), ZERO)
    after_lines = [n for _, n in keep] + adds
    after_net = sum((l.net_amount for l in after_lines), ZERO)
    after_total = sum((l.net_amount + l.tax_amount for l in after_lines), ZERO)
    per = _period(sub, today) if effective == "now" else None
    base = None
    if per:
        base = sub.invoices.filter(kind="recurring", period_start=per[1]).exclude(state="cancelled").first()
        if base is None or base.state == "draft":
            # Nothing has been issued for this period yet: no proration, a draft is simply regenerated with the new lines.
            if base is not None:
                notes.append("This period's invoice is still a draft, so it will be regenerated with the new lines. No separate adjustment is needed.")
            per = None
    frac = ZERO
    diffs = []
    adj_net = adj_tax = ZERO
    if per:
        idx, start, end = per
        days_total = (end - start).days + 1
        frac = Decimal((end - today).days + 1) / Decimal(days_total)
        entries = [(old, new) for old, new in keep] + [(None, a) for a in adds] + [(r, None) for r in removes]
        for old, new in entries:
            if old is not None and new is not None and not old.recurring:
                continue
            o_net = old.net_amount if old else ZERO
            n_net = new.net_amount if new else ZERO
            d = q((n_net - o_net) * frac)
            if d == 0:
                continue
            ref = new or old
            rate = ref.tax_rate
            t = q(d * rate / 100)
            desc = (new or old).description
            diffs.append(dict(description=f"{desc} · change from {today:%d %b} to {end:%d %b}", amount=d, tax=t, tax_name=ref.tax.name if ref.tax_id else "",
                              tax_rate=rate, product=(new or old).product, quantity=1))
            adj_net += d
            adj_tax += t
    adj_total = adj_net + adj_tax
    mrr_before = q(sum((l.net_amount for l in cur if l.recurring), ZERO) * Plan.MONTHLY_FACTOR[sub.plan.interval])
    mrr_after = q(sum((l.net_amount for l in after_lines if l.recurring), ZERO) * Plan.MONTHLY_FACTOR[plan.interval])
    return dict(plan=plan, quantity=qty, effective=effective, today=today, period=per, fraction=frac, notes=notes,
                before_net=before_net, before_total=before_total, after_net=after_net, after_total=after_total,
                adjust_net=adj_net, adjust_tax=adj_tax, adjust_total=adj_total, due_now=max(adj_total, ZERO), credit=max(-adj_total, ZERO),
                diffs=diffs, mrr_before=mrr_before, mrr_after=mrr_after, next_amount=after_total, regenerate=base,
                changed=bool(adds or removes or plan.pk != sub.plan_id or any((o.label, o.quantity, o.unit_price) != (n.label, n.quantity, n.unit_price) for o, n in keep)),
                addons_added=[a.product.name for a in adds], addons_removed=[r.product.name for r in removes])


def apply_change(sub, *, plan=None, quantity=None, addons=None, effective="now", actor=None, today=None):
    """Commit an amendment. effective='now' prorates the rest of the paid period; 'next' schedules it for the next billing date."""
    today = today or timezone.localdate()
    _require(sub, "change")
    sub = fresh(sub)
    pv = preview_change(sub, plan=plan, quantity=quantity, addons=addons, effective=effective, today=today)
    if not pv["changed"]:
        raise TransitionError("Nothing to change.")
    summary = _summary(sub, pv)
    if effective == "next" and sub.periods_billed:
        eff = sub.next_invoice_date
        payload = {"plan_id": pv["plan"].pk, "quantity": pv["quantity"], "addons": {str(k): v for k, v in (addons or {}).items()}}
        ScheduledChange.objects.create(subscription=sub, effective_date=eff, payload=payload, summary=summary, created_by=actor if getattr(actor, "pk", None) else None)
        record_change(sub, "terms", before=pv["mrr_before"], after=pv["mrr_before"], effective=today, detail=f"Scheduled for {eff:%d %b %Y}: {summary}", actor=actor, movement=SubscriptionChange.NONE)
        return {"scheduled_for": eff, "preview": pv, "document": None}
    with transaction.atomic():
        doc = _commit(sub, pv, plan, quantity, addons, actor, today, summary)
    automation.fire("plan_changed", fresh(sub), bucket=f"c{SubscriptionChange.objects.filter(subscription=sub).count()}")
    return {"scheduled_for": None, "preview": pv, "document": doc}


def _summary(sub, pv):
    bits = []
    if pv["plan"].pk != sub.plan_id:
        bits.append(f"{sub.plan.name} → {pv['plan'].name}")
    pl = _plan_line(sub)
    if pl and pl.quantity != pv["quantity"]:
        bits.append(f"seats {pl.quantity} → {pv['quantity']}")
    bits += [f"+{n}" for n in pv["addons_added"]] + [f"−{n}" for n in pv["addons_removed"]]
    return ", ".join(bits) or "terms"


def _commit(sub, pv, plan, quantity, addons, actor, today, summary):
    before = mrr_of(sub)
    plan_obj, qty, keep, adds, removes, _ = _target(sub, plan, quantity, addons)
    for old, new in keep:
        if old is not new:
            old.label, old.quantity, old.unit_price = new.label, new.quantity, new.unit_price
            old.save(update_fields=["label", "quantity", "unit_price"])
    for a in adds:
        a.save()
    for r in removes:
        r.delete()
    if plan_obj.pk != sub.plan_id:
        sub.plan = plan_obj
        sub.save(update_fields=["plan"])
    after = mrr_of(sub)
    kind = "plan" if pv["plan"].pk != fresh(sub).plan_id or "→" in summary else ("addon" if (adds or removes) and "seats" not in summary else "quantity")
    record_change(sub, kind, before=before, after=after, effective=today, detail=summary, actor=actor)
    audit.log(actor, "subscription.change", sub, summary)
    doc = None
    if pv.get("regenerate") is not None and pv["regenerate"].state == "draft":
        from billing import services as billing
        draft = pv["regenerate"]
        billing.cancel_invoice(draft)
        doc = billing.replace_invoice(draft)
    elif pv["period"] and pv["diffs"]:
        from billing import services as billing
        if pv["adjust_total"] > 0:
            doc = billing.create_adjustment_invoice(sub, pv["diffs"], pv["period"], today=today, actor=actor)
        elif pv["adjust_total"] < 0:
            base = sub.invoices.filter(kind="recurring", period_start=pv["period"][1]).exclude(state="cancelled").first()
            if base:
                doc = billing.issue_credit_note(base, amount=-pv["adjust_total"], reason=f"Proration: {summary}", actor=actor, cap=True)
    return doc


def apply_scheduled(sub, today=None):
    """Called by the billing run before pricing a due period: applies any amendment scheduled for or before it."""
    today = today or timezone.localdate()
    n = 0
    for sc in sub.scheduled_changes.filter(applied_at__isnull=True, cancelled_at__isnull=True, effective_date__lte=sub.next_invoice_date or today):
        p = sc.payload
        plan = Plan.objects.filter(pk=p.get("plan_id")).first()
        try:
            before = mrr_of(sub)
            plan_, qty, keep, adds, removes, _ = _target(fresh(sub), plan, p.get("quantity"), {int(k): v for k, v in (p.get("addons") or {}).items()})
            for old, new in keep:
                if old is not new:
                    old.label, old.quantity, old.unit_price = new.label, new.quantity, new.unit_price
                    old.save(update_fields=["label", "quantity", "unit_price"])
            for a in adds:
                a.save()
            for r in removes:
                r.delete()
            if plan_.pk != sub.plan_id:
                sub.plan = plan_
                sub.save(update_fields=["plan"])
            record_change(sub, "plan" if plan_.pk != sub.plan_id else "quantity", before=before, after=mrr_of(sub), effective=sc.effective_date, detail=f"scheduled: {sc.summary}")
            sc.applied_at = timezone.now()
        except TransitionError as e:
            sc.cancelled_at = timezone.now()
            sc.summary = f"{sc.summary} (not applied: {e})"[:300]
        sc.save(update_fields=["applied_at", "cancelled_at", "summary"])
        n += 1
    return n


def cancel_scheduled(sc, actor=None):
    sc.cancelled_at = timezone.now()
    sc.save(update_fields=["cancelled_at"])
    audit.log(actor, "subscription.change_cancelled", sc.subscription, sc.summary)


def set_addon(sub, product_id, on, *, actor=None):
    """Quick add-on switch. Before the first invoice it takes effect immediately; afterwards from the next billing period."""
    _require(sub, "addons")
    pid = int(product_id)
    if sub.state == Subscription.CONFIRMED or not sub.periods_billed:
        plan, qty, keep, adds, removes, _ = _target(fresh(sub), None, None, {pid: bool(on)})
        before = mrr_of(sub)
        for a in adds:
            a.save()
        for r in removes:
            r.delete()
        record_change(sub, "addon", before=before, after=mrr_of(sub), detail=("+" if on else "−") + str(Product.objects.get(pk=pid)), actor=actor)
        return None
    return apply_change(sub, addons={pid: bool(on)}, effective="next", actor=actor)


def record_edit(sub, before_mrr, actor=None, detail="Lines or terms edited"):
    """Used after staff edit lines/terms in the form: stores the MRR movement the edit caused."""
    after = mrr_of(sub)
    record_change(sub, "price" if after != before_mrr else "terms", before=before_mrr, after=after, detail=detail, actor=actor)
