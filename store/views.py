"""Public subscription storefront: published plans only, server-side pricing, checkout creates a real accepted contract."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing
from catalog.models import Plan
from subscriptions import services
from subscriptions.models import Subscription

from . import services as storefront


def published_plans():
    today = timezone.localdate()
    return [p for p in Plan.objects.filter(active=True, published=True).prefetch_related("tiers", "products") if p.is_available(today)]


def home(request):
    plans = published_plans()
    order = {"daily": 0, "weekly": 1, "monthly": 2, "yearly": 3}
    plans.sort(key=lambda p: (order[p.interval], p.price))
    return render(request, "store/home.html", {"plans": plans, "title": "Plans"})


def plan_detail(request, pk):
    plan = next((p for p in published_plans() if p.pk == pk), None)
    if plan is None:
        from django.http import Http404
        raise Http404
    src = request.GET
    error = None
    try:
        qty, addons, bundled, ca, cb, variants = storefront.selection(plan, src)
        lines, net, tx, total = storefront.price(plan, qty, ca, cb, variants)
    except ValidationError as exc:
        error = " ".join(exc.messages)
        qty, addons, bundled, ca, cb, variants = storefront.selection(plan, QueryDict())
        lines, net, tx, total = storefront.price(plan, qty, ca, cb, variants)
    ctx = {"plan": plan, "qty": qty, "addons": addons, "bundled": bundled, "chosen_addons": {a.pk for a in ca}, "chosen_bundled": {b.pk for b in cb}, "variants": variants,
           "lines": lines, "net": net, "tax": tx, "total": total, "title": plan.name, "selection_error": error}
    if request.user.is_authenticated and request.user.role == "customer":
        ctx["checkout_token"] = storefront.checkout_token(request.user, plan)
    tpl = "store/_summary.html" if request.headers.get("HX-Target") == "summary" else "store/plan.html"
    return render(request, tpl, ctx)


@login_required
@require_POST
def checkout(request, pk):
    plan = next((p for p in published_plans() if p.pk == pk), None)
    if plan is None:
        messages.error(request, "That plan is no longer available.")
        return redirect("store:home")
    user = request.user
    if user.role != "customer" or not hasattr(user, "customer"):
        messages.error(request, "Sign in with a customer account to subscribe. Staff can create quotations from Sales.")
        return redirect("store:plan", pk=pk)
    name = request.POST.get("signed_name", "").strip()
    if not name or len(name) > 160 or not request.POST.get("agree"):
        messages.error(request, "Type your full name and tick the box to accept the order.")
        from django.urls import reverse
        choices = QueryDict(mutable=True)
        for key in request.POST:
            if key in ("quantity", "addon") or key.startswith(("variant_", "bundle_")):
                choices.setlist(key, request.POST.getlist(key))
        return redirect(f"{reverse('store:plan', args=[pk])}?{choices.urlencode()}")
    try:
        sub = storefront.purchase(user=user, plan=plan, source=request.POST, signed_name=name, ip=request.META.get("REMOTE_ADDR"))
    except (ValidationError, services.TransitionError) as exc:
        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
        return redirect("store:plan", pk=pk)
    sub = Subscription.objects.select_related("plan", "customer").get(pk=sub.pk)
    if sub.state == Subscription.ACTIVE:
        billing.run_billing(trigger="storefront", only=[sub.pk])  # first invoice straight away
        inv = sub.invoices.exclude(kind="credit_note").first()
        if inv:
            if inv.state == "draft":
                billing.send_invoice(inv)
            messages.success(request, f"Welcome aboard! {sub.number} is active. Your first invoice is ready.")
            return redirect("core:portal_pay", pk=inv.pk)
    elif sub.plan.activation == Plan.ACT_PAYMENT:
        inv = sub.invoices.first()
        messages.success(request, f"Order received. Pay your first invoice to activate {sub.number}.")
        return redirect("core:portal_pay", pk=inv.pk) if inv else redirect("core:portal_subscription", pk=sub.pk)
    else:
        messages.success(request, f"Order {sub.number} received. We'll activate it shortly.")
    return redirect("core:portal_subscription", pk=sub.pk)
