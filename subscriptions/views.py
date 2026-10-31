import json

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Plan, Product, ProductVariant
from core import audit
from core.crud import apply_sort, paginate
from core.permissions import internal_required

from . import services
from .forms import LineFormSet, LiveSubscriptionForm, NewSubscriptionForm, SubscriptionHeaderForm
from .models import Subscription

ACTIONS = {"send": services.send_quotation, "confirm": services.confirm, "activate": services.activate,
           "pause": services.pause, "resume": services.resume, "close": services.close}


@internal_required
def subscription_list(request):
    qs = Subscription.objects.select_related("customer", "plan")
    q, state, plan = (request.GET.get(k, "").strip() for k in ("q", "state", "plan"))
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer__company__icontains=q))
    if state:
        qs = qs.filter(state=state)
    if plan:
        qs = qs.filter(plan_id=plan)
    counts = dict(Subscription.objects.values_list("state").annotate(n=Count("id")))
    if request.GET.get("view") == "board":
        cols = []
        for k, label in Subscription.STATES:
            col = Subscription.objects.filter(state=k).select_related("customer", "plan")
            if q:
                col = col.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer__company__icontains=q))
            cols.append({"key": k, "label": label, "n": counts.get(k, 0), "items": col[:12]})
        return render(request, "subscriptions/board.html", {"cols": cols, "q": q, "total": sum(counts.values())})
    qs, sort = apply_sort(request, qs, {"number": "number", "customer": "customer__name", "start": "start_date", "expiry": "expiry_date", "next": "next_invoice_date"}, "-number")
    return render(request, "subscriptions/list.html", {
        "sort": sort, "page": paginate(request, qs), "q": q, "state": state, "plan": plan, "plans": Plan.objects.all(),
        "states": Subscription.STATES, "counts": counts, "total": sum(counts.values())})


@internal_required
def subscription_new(request):
    from core.models import CompanySettings
    initial = {"customer": request.GET.get("customer"), "payment_terms": CompanySettings.get().default_payment_terms}
    form = NewSubscriptionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        sub = services.create_subscription(customer=d["customer"], plan=d["plan"], template=d["template"],
                                           start_date=d["start_date"], expiry_date=d["expiry_date"],
                                           payment_terms=d["payment_terms"], notes=d["notes"], user=request.user)
        audit.log(request.user, "subscription.create", sub, f"{sub.customer} on {sub.plan.name}")
        messages.success(request, f"{sub.number} created as a draft. Review its lines, then send the quotation.")
        return redirect("subscriptions:detail", pk=sub.pk)
    plans = {p.pk: {"templates": list(p.templates.filter(active=True).values_list("pk", flat=True))} for p in Plan.objects.all()}
    return render(request, "subscriptions/new.html", {"form": form, "plans_json": json.dumps(plans)})


def _get(pk):
    return get_object_or_404(Subscription.objects.select_related("customer", "plan"), pk=pk)


@internal_required
def subscription_detail(request, pk):
    sub = _get(pk)
    return render(request, "subscriptions/detail.html", {
        "sub": sub, "lines": sub.lines.select_related("product", "variant", "tax"),
        "invoices": sub.invoices.all()[:12], "stages": Subscription.FLOW,
        "addons": sub.plan.products.filter(type=Product.ADDON, active=True),
        "enabled_addons": set(sub.lines.filter(product__type=Product.ADDON).values_list("product_id", flat=True)),
        "renewals": sub.renewals.all(), "timeline": audit.timeline(sub)})


@internal_required
def subscription_edit(request, pk):
    sub = _get(pk)
    if not sub.can_edit:
        messages.error(request, "A closed subscription can't be edited. Renew it instead.")
        return redirect("subscriptions:detail", pk=pk)
    form = (SubscriptionHeaderForm if sub.is_editable else LiveSubscriptionForm)(request.POST or None, instance=sub)
    fs = LineFormSet(request.POST or None, instance=sub)
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        before = {l.pk: l.amount for l in sub.lines.all()}
        form.save()
        fs.save()
        if not sub.is_editable and request.POST.get("prorate"):
            from billing.services import create_adjustment_invoice
            adj = create_adjustment_invoice(Subscription.objects.get(pk=sub.pk), before, user=request.user)
            if adj:
                messages.success(request, f"Prorated adjustment {adj.number} created as a draft ({adj.total}).")
        audit.log(request.user, "subscription.edit", sub, "lines/terms changed" + ("" if sub.is_editable else "; applies from next invoice"))
        messages.success(request, "Saved." + ("" if sub.is_editable else " Changes apply from the next invoice."))
        return redirect("subscriptions:detail", pk=pk)
    prices = {p.pk: str(p.sales_price) for p in Product.objects.all()}
    variants = {v.pk: {"product": v.product_id, "extra": str(v.extra_price)} for v in
                ProductVariant.objects.all()}
    return render(request, "subscriptions/edit.html", {
        "sub": sub, "form": form, "formset": fs, "prices_json": json.dumps(prices), "variants_json": json.dumps(variants)})


@internal_required
def subscription_print(request, pk):
    sub = _get(pk)
    return render(request, "subscriptions/print.html", {"sub": sub, "lines": sub.lines.select_related("product", "variant", "tax")})


@internal_required
@require_POST
def subscription_action(request, pk, action):
    sub = _get(pk)
    try:
        if action == "renew":
            new = services.renew(sub)
            audit.log(request.user, "subscription.renew", sub, f"renewed as {new.number}")
            messages.success(request, f"Renewed as {new.number}, starting {new.start_date:%d %b %Y}.")
            return redirect("subscriptions:detail", pk=new.pk)
        if action in ("addon_on", "addon_off"):
            services.set_addon(sub, request.POST.get("product"), action == "addon_on")
            audit.log(request.user, f"subscription.{action}", sub, f"product {request.POST.get('product')}")
            messages.success(request, "Add-on updated. It applies from the next invoice.")
        else:
            ACTIONS[action](sub)
            audit.log(request.user, f"subscription.{action}", sub)
            if action == "send":
                try:
                    services.email_quotation(sub, request)
                    messages.success(request, f"Quotation {sub.number} emailed to {sub.customer.email}.")
                except Exception as e:
                    messages.warning(request, f"Quotation moved to Quotation stage, but the email failed: {e}")
            else:
                messages.success(request, f"{sub.number}: {action} done.")
    except (services.TransitionError, KeyError) as e:
        messages.error(request, str(e) if isinstance(e, services.TransitionError) else "Unknown action.")
    return redirect("subscriptions:detail", pk=pk)
