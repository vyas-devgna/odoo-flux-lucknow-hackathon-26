import json

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Plan, Product, ProductVariant
from core.crud import paginate
from core.permissions import internal_required

from . import services
from .forms import LineFormSet, NewSubscriptionForm, SubscriptionHeaderForm
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
    return render(request, "subscriptions/list.html", {
        "page": paginate(request, qs), "q": q, "state": state, "plan": plan, "plans": Plan.objects.all(),
        "states": Subscription.STATES, "counts": counts, "total": sum(counts.values())})


@internal_required
def subscription_new(request):
    initial = {"customer": request.GET.get("customer")}
    form = NewSubscriptionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        sub = services.create_subscription(customer=d["customer"], plan=d["plan"], template=d["template"],
                                           start_date=d["start_date"], expiry_date=d["expiry_date"],
                                           payment_terms=d["payment_terms"], notes=d["notes"], user=request.user)
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
        "renewals": sub.renewals.all()})


@internal_required
def subscription_edit(request, pk):
    sub = _get(pk)
    if not sub.is_editable:
        messages.error(request, "Lines can be edited only while a subscription is a draft or quotation.")
        return redirect("subscriptions:detail", pk=pk)
    form = SubscriptionHeaderForm(request.POST or None, instance=sub)
    fs = LineFormSet(request.POST or None, instance=sub)
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        form.save()
        fs.save()
        messages.success(request, "Saved.")
        return redirect("subscriptions:detail", pk=pk)
    prices = {p.pk: str(p.sales_price) for p in Product.objects.all()}
    variants = {v.pk: {"product": v.product_id, "extra": str(v.extra_price)} for v in
                ProductVariant.objects.all()}
    return render(request, "subscriptions/edit.html", {
        "sub": sub, "form": form, "formset": fs, "prices_json": json.dumps(prices), "variants_json": json.dumps(variants)})


@internal_required
@require_POST
def subscription_action(request, pk, action):
    sub = _get(pk)
    try:
        if action == "renew":
            new = services.renew(sub)
            messages.success(request, f"Renewed as {new.number}, starting {new.start_date:%d %b %Y}.")
            return redirect("subscriptions:detail", pk=new.pk)
        if action in ("addon_on", "addon_off"):
            services.set_addon(sub, request.POST.get("product"), action == "addon_on")
            messages.success(request, "Add-on updated. It applies from the next invoice.")
        else:
            ACTIONS[action](sub)
            messages.success(request, f"{sub.number}: {action} done.")
    except (services.TransitionError, KeyError) as e:
        messages.error(request, str(e) if isinstance(e, services.TransitionError) else "Unknown action.")
    return redirect("subscriptions:detail", pk=pk)
