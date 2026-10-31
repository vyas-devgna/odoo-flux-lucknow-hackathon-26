"""Public subscription storefront: published plans only, server-side pricing, checkout creates a real accepted contract."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as billing
from catalog.models import Plan, Product, ProductVariant, Tax
from core import audit
from core.money import q
from subscriptions import services
from subscriptions.models import Subscription, SubscriptionLine


def published_plans():
    today = timezone.localdate()
    return [p for p in Plan.objects.filter(active=True, published=True).prefetch_related("tiers", "products") if p.is_available(today)]


def home(request):
    plans = published_plans()
    order = {"daily": 0, "weekly": 1, "monthly": 2, "yearly": 3}
    plans.sort(key=lambda p: (order[p.interval], p.price))
    return render(request, "store/home.html", {"plans": plans, "title": "Plans"})


def _offered(plan):
    addons = [p for p in plan.products.filter(active=True, published=True, type=Product.ADDON)]
    bundled = [p for p in plan.products.filter(active=True, published=True).exclude(type=Product.ADDON).prefetch_related("variants")]
    return addons, bundled


def _selection(plan, src):
    try:
        qty = max(int(src.get("quantity") or plan.min_quantity), plan.min_quantity)
    except ValueError:
        qty = plan.min_quantity
    addons, bundled = _offered(plan)
    chosen_addons = [a for a in addons if str(a.pk) in src.getlist("addon")]
    variants = {}
    for b in bundled:
        v = src.get(f"variant_{b.pk}")
        if v and b.variants.filter(pk=v).exists():
            variants[b.pk] = int(v)
    chosen_bundled = [b for b in bundled if src.get(f"bundle_{b.pk}")]
    return qty, addons, bundled, chosen_addons, chosen_bundled, variants


def _price(plan, qty, chosen_addons, chosen_bundled, variants):
    day = timezone.localdate()
    tax = Tax.default(day)
    lines = [SubscriptionLine(label=plan.name, quantity=qty, unit_price=plan.unit_price_for(qty), tax=tax, is_plan_line=True)]
    for b in chosen_bundled:
        v = ProductVariant.objects.filter(pk=variants.get(b.pk)).first()
        lines.append(SubscriptionLine(product=b, variant=v, quantity=1, unit_price=v.price if v else b.sales_price, tax=b.tax or tax, recurring=b.recurring))
    for a in chosen_addons:
        lines.append(SubscriptionLine(product=a, quantity=1, unit_price=a.sales_price, tax=a.tax or tax, recurring=a.recurring))
    net = sum((l.net_amount for l in lines), Decimal(0))
    tx = sum((l.tax_amount for l in lines), Decimal(0))
    return lines, net, tx, net + tx


def plan_detail(request, pk):
    plan = next((p for p in published_plans() if p.pk == pk), None)
    if plan is None:
        from django.http import Http404
        raise Http404
    src = request.GET
    qty, addons, bundled, ca, cb, variants = _selection(plan, src)
    lines, net, tx, total = _price(plan, qty, ca, cb, variants)
    ctx = {"plan": plan, "qty": qty, "addons": addons, "bundled": bundled, "chosen_addons": {a.pk for a in ca}, "chosen_bundled": {b.pk for b in cb}, "variants": variants,
           "lines": lines, "net": net, "tax": tx, "total": total, "title": plan.name}
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
    if not name or not request.POST.get("agree"):
        messages.error(request, "Type your full name and tick the box to accept the order.")
        from django.urls import reverse
        return redirect(f"{reverse('store:plan', args=[pk])}?{request.POST.urlencode()}")
    qty, addons, bundled, ca, cb, variants = _selection(plan, request.POST)
    lines, *_ = _price(plan, qty, ca, cb, variants)
    sub = services.create_subscription(customer=user.customer, plan=plan, quantity=qty, user=user, start_date=timezone.localdate(), notes="Ordered from the storefront")
    for l in lines[1:]:
        SubscriptionLine.objects.create(subscription=sub, product=l.product, variant=l.variant, quantity=1, unit_price=l.unit_price, tax=l.tax, recurring=l.recurring)
    services.send_quotation(sub, actor=user)  # draft -> quotation, then accepted in the same step
    sub = services.accept(Subscription.objects.get(pk=sub.pk), actor=user, signed_name=name, ip=request.META.get("REMOTE_ADDR"), how="Accepted at storefront checkout")
    audit.log(user, "store.checkout", sub, plan.name)
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
