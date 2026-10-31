import json

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalog.models import Plan, Product, ProductVariant
from core import audit
from core.crud import apply_sort, paginate
from core.permissions import cap_required

from . import services
from .forms import CloseForm, LineFormSet, LiveSubscriptionForm, NewSubscriptionForm, SubscriptionHeaderForm
from .models import ScheduledChange, Subscription, SubscriptionChange

sales_required = cap_required("sales")
view_required = cap_required("sales", "billing", "finance")


def _search(qs, q):
    return qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer__company__icontains=q)) if q else qs


@view_required
def subscription_list(request):
    qs = Subscription.objects.select_related("customer", "plan").exclude(state__in=[Subscription.DRAFT, Subscription.QUOTATION, Subscription.CANCELLED])
    q, state, plan = (request.GET.get(k, "").strip() for k in ("q", "state", "plan"))
    qs = _search(qs, q)
    if state:
        qs = qs.filter(state=state)
    if plan:
        qs = qs.filter(plan_id=plan)
    if request.GET.get("flag") == "exception":
        qs = qs.filter(payment_exception=True)
    counts = dict(Subscription.objects.values_list("state").annotate(n=Count("id")))
    qs, sort = apply_sort(request, qs, {"number": "number", "customer": "customer__name", "start": "start_date", "expiry": "expiry_date", "next": "next_invoice_date", "state": "state"}, "-number")
    states = [(k, l) for k, l in Subscription.STATES if k in ("confirmed", "active", "paused", "closed")]
    from core import reports
    today = timezone.localdate()
    soon = Subscription.objects.filter(state="active", expiry_date__gte=today, expiry_date__lte=today + timezone.timedelta(days=30)).count()
    mrr = reports.mrr_at(today)
    strip = [{"label": "Active contracts", "value": counts.get("active", 0), "sub": f"{counts.get('paused', 0)} paused"},
             {"label": "Monthly recurring", "value": f"₹{mrr:,.0f}", "sub": f"ARR ₹{mrr * 12:,.0f}", "href": reverse("core:report_mrr") if request.user.has_cap("finance") else ""},
             {"label": "Ending in 30 days", "value": soon, "sub": "Renewals to decide", "href": reverse("subscriptions:renewals")},
             {"label": "Payment exceptions", "value": Subscription.objects.filter(payment_exception=True, state__in=["active", "paused"]).count(), "sub": "Overdue or failed", "tone": "bad", "href": "?flag=exception"}]
    return render(request, "subscriptions/list.html", {"strip": strip,
        "sort": sort, "page": paginate(request, qs), "q": q, "state": state, "plan": plan, "plans": Plan.objects.all(), "states": states, "counts": counts,
        "total": sum(counts.get(k, 0) for k, _ in states), "exceptions": Subscription.objects.filter(payment_exception=True, state__in=["active", "paused"]).count(),
        "title": "Subscriptions"})


@sales_required
def quotes(request):
    qs = Subscription.objects.select_related("customer", "plan", "salesperson").filter(state__in=[Subscription.DRAFT, Subscription.QUOTATION, Subscription.CONFIRMED, Subscription.CANCELLED])
    q, state = request.GET.get("q", "").strip(), request.GET.get("state", "")
    qs = _search(qs, q)
    counts = dict(Subscription.objects.values_list("state").annotate(n=Count("id")))
    if state:
        qs = qs.filter(state=state)
    qs, sort = apply_sort(request, qs, {"number": "number", "customer": "customer__name", "valid": "quote_valid_until", "state": "state"}, "-number")
    states = [(k, l) for k, l in Subscription.STATES if k in ("draft", "quotation", "confirmed", "cancelled")]
    value = qs.exclude(state=Subscription.CANCELLED).count()
    return render(request, "subscriptions/quotes.html", {"page": paginate(request, qs), "q": q, "state": state, "states": states, "counts": counts, "sort": sort, "title": "Quotations"})


@sales_required
def pipeline(request):
    q = request.GET.get("q", "").strip()
    counts = dict(Subscription.objects.values_list("state").annotate(n=Count("id")))
    cols = []
    for k, label in Subscription.STATES:
        if k in ("closed", "cancelled"):
            continue
        col = _search(Subscription.objects.filter(state=k).select_related("customer", "plan").prefetch_related("lines__tax", "lines__product"), q)
        items = list(col[:12])
        cols.append({"key": k, "label": label, "n": counts.get(k, 0), "items": items, "mrr": sum((s.recurring_monthly for s in items), 0)})
    return render(request, "subscriptions/board.html", {"cols": cols, "q": q, "title": "Pipeline"})


@view_required
def renewals(request):
    from core import reports
    today = timezone.localdate()
    qs = (Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__isnull=False, expiry_date__lte=today + timezone.timedelta(days=120))
          .select_related("customer", "plan").prefetch_related("renewals", "lines__tax", "lines__product").order_by("expiry_date"))
    return render(request, "subscriptions/renewals.html", {"page": paginate(request, qs), "today": today, "forecast": reports.renewal_forecast(), "title": "Renewals"})


@view_required
def changes(request):
    mv = request.GET.get("movement", "")
    qs = SubscriptionChange.objects.select_related("subscription__customer", "actor").exclude(kind="created")
    if mv:
        qs = qs.filter(movement=mv)
    qs = qs.order_by("-effective_date", "-id")
    scheduled = ScheduledChange.objects.filter(applied_at__isnull=True, cancelled_at__isnull=True).select_related("subscription__customer")
    return render(request, "subscriptions/changes.html", {"page": paginate(request, qs), "scheduled": scheduled, "movement": mv, "title": "Changes"})


@sales_required
def subscription_new(request):
    from core.models import CompanySettings
    initial = {"customer": request.GET.get("customer"), "payment_terms": CompanySettings.get().default_payment_terms, "salesperson": request.user.pk}
    form = NewSubscriptionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        sub = services.create_subscription(customer=d["customer"], plan=d["plan"], template=d["template"], start_date=d["start_date"], expiry_date=d["expiry_date"],
                                           payment_terms=d["payment_terms"] or None, notes=d["notes"], user=request.user, quantity=d["quantity"], salesperson=d["salesperson"])
        audit.log(request.user, "subscription.create", sub, f"{sub.customer} on {sub.plan.name}")
        messages.success(request, f"{sub.number} created as a draft. Review its lines, then send the quotation.")
        return redirect("subscriptions:detail", pk=sub.pk)
    plans = {p.pk: {"templates": list(p.templates.filter(active=True).values_list("pk", flat=True)), "min": p.min_quantity} for p in Plan.objects.all()}
    help_ = ["Pick the customer and plan, optionally a template to pre-fill the product lines.", "A draft is private until you send it; sending emails the customer a link to review and accept it.",
             "Accepting records who agreed, when, and the exact total, then the contract can start billing.", "You can create an alternative quote later if the customer wants options."]
    return render(request, "subscriptions/new.html", {"form": form, "plans_json": json.dumps(plans), "title": "New quotation", "help": help_})


def _get(pk):
    return get_object_or_404(Subscription.objects.select_related("customer", "plan", "salesperson"), pk=pk)


@view_required
def subscription_detail(request, pk):
    sub = _get(pk)
    lines = list(sub.lines.select_related("product", "variant", "tax"))
    return render(request, "subscriptions/detail.html", {
        "sub": sub, "lines": lines, "invoices": sub.invoices.exclude(kind="credit_note")[:12], "stages": Subscription.FLOW,
        "addons": sub.plan.products.filter(type=Product.ADDON, active=True),
        "enabled_addons": {l.product_id for l in lines if l.product_id and l.is_addon}, "renewals": sub.renewals.all(), "alternatives": sub.alternatives.all(),
        "changes": sub.changes.order_by("-effective_date", "-id")[:10], "scheduled": sub.scheduled_changes.filter(applied_at__isnull=True, cancelled_at__isnull=True),
        "acceptance": sub.acceptances.first(), "close_form": CloseForm(), "title": sub.number,
        "mrr": sub.recurring_monthly, "tot": {"subtotal": sum((l.net_amount for l in lines), 0), "tax": sum((l.tax_amount for l in lines), 0)}})


@sales_required
def subscription_edit(request, pk):
    sub = _get(pk)
    if not sub.can_edit:
        messages.error(request, "A closed subscription can't be edited. Renew it instead.")
        return redirect("subscriptions:detail", pk=pk)
    form = (SubscriptionHeaderForm if sub.is_editable else LiveSubscriptionForm)(request.POST or None, instance=sub)
    fs = LineFormSet(request.POST or None, instance=sub)
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        before = services.mrr_of(sub)
        form.save()
        for inst in fs.save(commit=False):
            if inst.product_id and not inst.pk:
                inst.recurring = inst.product.recurring if "recurring" not in fs.forms[0].changed_data else inst.recurring
            inst.subscription = sub
            inst.save()
        for obj in fs.deleted_objects:
            obj.delete()
        if sub.state in (Subscription.ACTIVE, Subscription.PAUSED):
            services.record_edit(sub, before, request.user)
        if sub.is_editable and sub.state == Subscription.DRAFT:
            pass
        audit.log(request.user, "subscription.edit", sub, "lines/terms changed")
        messages.success(request, "Saved." + ("" if sub.is_editable else " Changes apply from the next invoice; issued invoices never change."))
        return redirect("subscriptions:detail", pk=pk)
    prices = {p.pk: str(p.sales_price) for p in Product.objects.all()}
    variants = {v.pk: {"product": v.product_id, "extra": str(v.extra_price)} for v in ProductVariant.objects.all()}
    help_ = (["Lines and prices can be edited freely while this is a draft or quotation.", "Revising a sent quotation bumps its revision so an old acceptance can't be reused."] if sub.is_editable else
             ["Changes here apply from the next invoice; issued invoices never change.", "Use Change plan or seats for immediate, prorated amendments with a cost preview.", "Every edit is recorded in the contract history and the recurring-revenue movements."])
    return render(request, "subscriptions/edit.html", {"help": help_, "timeline": audit.timeline(sub), "sub": sub, "form": form, "formset": fs, "prices_json": json.dumps(prices), "variants_json": json.dumps(variants), "title": f"Edit {sub.number}"})


ACTIONS = {"send": services.send_quotation, "confirm": services.confirm, "activate": services.activate, "pause": services.pause, "resume": services.resume}


@sales_required
@require_POST
def subscription_action(request, pk, action):
    sub = _get(pk)
    try:
        if action == "close":
            form = CloseForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Choose a reason for closing.")
                return redirect("subscriptions:detail", pk=pk)
            d = form.cleaned_data
            services.close(sub, actor=request.user, reason=d["reason"], note=d["note"], immediate=True if d["immediate"] else None)
            audit.log(request.user, "subscription.close", sub, d["reason"])
            messages.success(request, f"{sub.number}: " + ("cancellation scheduled for the end of the paid period." if fresh_pending(sub) else "closed."))
        elif action == "keep":
            services.keep(sub, request.user)
            messages.success(request, "Scheduled cancellation withdrawn.")
        elif action == "reject":
            services.reject(sub, actor=request.user, reason=request.POST.get("reason", ""))
            audit.log(request.user, "subscription.reject", sub)
            messages.success(request, f"{sub.number} marked as rejected.")
        elif action == "resend":
            if sub.state != Subscription.QUOTATION:
                raise services.TransitionError("Only a sent quotation can be re-emailed.")
            services.email_quotation(sub, resend=True)
            audit.log(request.user, "subscription.resend", sub)
            messages.success(request, f"Quotation queued for {sub.customer.email}.")
        elif action == "revise":
            services.revise_quotation(sub, request.user)
            audit.log(request.user, "subscription.revise", sub)
            messages.success(request, "Quotation reopened for editing (revision bumped).")
            return redirect("subscriptions:edit", pk=pk)
        elif action in ("renew", "renew_now"):
            new = services.renew(sub, actor=request.user, accept=(action == "renew_now"))
            audit.log(request.user, "subscription.renew", sub, f"renewed as {new.number}")
            messages.success(request, f"Renewal {'started' if action == 'renew_now' else 'quotation created'}: {new.number}, term starts {new.start_date:%d %b %Y}.")
            return redirect("subscriptions:detail", pk=new.pk)
        elif action in ("alternative", "upsell"):
            new = services.clone_as_quote(sub, user=request.user, kind=action)
            audit.log(request.user, f"subscription.{action}", sub, new.number)
            messages.success(request, f"{action.title()} quotation {new.number} created.")
            return redirect("subscriptions:edit", pk=new.pk)
        elif action in ("addon_on", "addon_off"):
            res = services.set_addon(sub, request.POST.get("product"), action == "addon_on", actor=request.user)
            audit.log(request.user, f"subscription.{action}", sub, f"product {request.POST.get('product')}")
            messages.success(request, "Add-on updated." + (f" It takes effect on {res['scheduled_for']:%d %b %Y}." if res and res.get("scheduled_for") else ""))
        elif action == "cancel_scheduled":
            services.cancel_scheduled(get_object_or_404(sub.scheduled_changes, pk=request.POST.get("id")), request.user)
            messages.success(request, "Scheduled change cancelled.")
        elif action in ACTIONS:
            res = ACTIONS[action](sub, actor=request.user) if action not in ("send",) else ACTIONS[action](sub, request.user)
            audit.log(request.user, f"subscription.{action}", sub)
            messages.success(request, f"Quotation {sub.number} sent to {sub.customer.email}." if action == "send" else f"{sub.number}: {action} done.")
        else:
            raise Http404
    except services.TransitionError as e:
        messages.error(request, str(e))
    return redirect("subscriptions:detail", pk=pk)


def fresh_pending(sub):
    return Subscription.objects.filter(pk=sub.pk).values_list("pending_cancel_at", flat=True).first()


@sales_required
def subscription_change(request, pk):
    """Plan / seats / add-ons wizard with a live cost preview (the preview partial is swapped by HTMX on every change)."""
    sub = _get(pk)
    if not sub.can("change"):
        messages.error(request, "Only active or paused subscriptions can be amended.")
        return redirect("subscriptions:detail", pk=pk)
    return change_view(request, sub, portal=False)


def change_view(request, sub, portal):
    plan_line = next((l for l in sub.lines.all() if l.is_plan_line), None)
    addon_ids = {l.product_id for l in sub.lines.all() if l.product_id and l.is_addon}
    src = request.POST if request.method == "POST" else request.GET
    plans = Plan.objects.filter(active=True, interval=sub.plan.interval)
    if portal:
        plans = plans.filter(published=True) | plans.filter(pk=sub.plan_id)
    try:
        plan = plans.filter(pk=src.get("plan") or sub.plan_id).first() or sub.plan
        qty = int(src.get("quantity") or (plan_line.quantity if plan_line else 1))
    except ValueError:
        plan, qty = sub.plan, 1
    offered = Product.objects.filter(pk__in=plan.products.values("pk"), type=Product.ADDON, active=True)
    chosen = {p.pk for p in offered if (str(p.pk) in src.getlist("addon") if (src.get("touched") or request.method == "POST") else p.pk in addon_ids)}
    addons = {p.pk: p.pk in chosen for p in offered}
    addons.update({pid: False for pid in addon_ids if pid not in addons})
    effective = src.get("effective", "now")
    error = pv = None
    try:
        pv = services.preview_change(sub, plan=plan, quantity=qty, addons=addons, effective=effective)
    except services.TransitionError as e:
        error = str(e)
    if request.method == "POST" and request.POST.get("commit") and pv:
        try:
            res = services.apply_change(sub, plan=plan, quantity=qty, addons=addons, effective=effective, actor=request.user)
            doc = res["document"]
            msg = "Plan updated." if not res["scheduled_for"] else f"Change scheduled for {res['scheduled_for']:%d %b %Y}."
            if doc:
                msg += f" {'Adjustment invoice' if doc.kind == 'adjustment' else 'Credit note'} {doc.number} created."
            messages.success(request, msg)
            return redirect("core:portal_subscription" if portal else "subscriptions:detail", pk=sub.pk)
        except services.TransitionError as e:
            error = str(e)
    ctx = {"sub": sub, "plans": plans, "plan": plan, "qty": qty, "offered": offered, "chosen": chosen, "effective": effective, "pv": pv, "error": error,
           "portal": portal, "min_qty": plan.min_quantity, "title": "Change plan or seats"}
    tpl = "subscriptions/_preview.html" if request.headers.get("HX-Target") == "preview" else "subscriptions/change.html"
    return render(request, tpl, ctx)


def quotation_ctx(request, sub):
    lines = list(sub.lines.select_related("product", "variant", "tax"))
    return {"sub": sub, "lines": lines, "subtotal": sum((l.net_amount for l in lines), 0), "taxtotal": sum((l.tax_amount for l in lines), 0),
            "acceptance": sub.acceptances.first(), "embed": bool(request.GET.get("embed")), "title": sub.number}


@sales_required
def subscription_print(request, pk):
    sub = _get(pk)
    return render(request, "documents/quotation.html", quotation_ctx(request, sub))
