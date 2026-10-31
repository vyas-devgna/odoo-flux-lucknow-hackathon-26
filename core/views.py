from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Customer
from billing.models import Invoice
from catalog.models import Plan, Product
from subscriptions import services
from subscriptions.models import Subscription

from . import reports
from .crud import paginate
from .permissions import customer_required, internal_required


def home(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    return redirect("core:dashboard" if request.user.is_internal else "core:portal")


@internal_required
def dashboard(request):
    today = timezone.localdate()
    s = reports.summary()
    series = reports.month_series()
    collected_rate = int(s["cash"] / s["invoiced"] * 100) if s["invoiced"] else 0
    overdue = (Invoice.objects.filter(state=Invoice.CONFIRMED, due_date__lt=today, amount_paid__lt=F("total"))
               .select_related("subscription").order_by("due_date")[:6])
    renewals = (Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__lte=today + timedelta(days=45),
                                            expiry_date__gte=today).select_related("customer", "plan").order_by("expiry_date")[:6])
    return render(request, "core/dashboard.html", {
        "s": s, "chart": reports.chart(series), "bars": reports.week_bars(), "rate": min(collected_rate, 100),
        "overdue": overdue, "renewals": renewals, "today": today,
        "drafts": Invoice.objects.filter(state=Invoice.DRAFT).count(),
        "due_now": Subscription.objects.filter(state="active", next_invoice_date__lte=today).count(),
        "recent": Invoice.objects.select_related("subscription").order_by("-id")[:6]})


@internal_required
def finance_reports(request):
    g = request.GET
    plan = g.get("plan") or None
    s = reports.summary(reports.parse_date(g.get("from")), reports.parse_date(g.get("to")), plan)
    status = g.get("view", "overdue")
    qs = Invoice.objects.select_related("subscription", "customer")
    if status == "overdue":
        qs = qs.filter(state=Invoice.CONFIRMED, due_date__lt=timezone.localdate(), amount_paid__lt=F("total")).order_by("due_date")
    elif status == "outstanding":
        qs = qs.filter(state=Invoice.CONFIRMED).order_by("due_date")
    else:
        qs = qs.filter(state=Invoice.PAID).order_by("-id")
    if plan:
        qs = qs.filter(subscription__plan_id=plan)
    return render(request, "core/reports.html", {
        "s": s, "page": paginate(request, qs), "view": status,
        "views": [("overdue", "Overdue"), ("outstanding", "Outstanding"), ("paid", "Paid")], "plans": Plan.objects.all(), "plan": plan or "",
        "from_": s["start"].isoformat(), "to": s["end"].isoformat(), "today": timezone.localdate()})


# ---- customers (staff) ----
@internal_required
def customer_list(request):
    q = request.GET.get("q", "").strip()
    qs = Customer.objects.annotate(n_subs=Count("subscriptions", distinct=True))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q))
    return render(request, "core/customers.html", {"page": paginate(request, qs), "q": q})


@internal_required
def customer_form(request, pk=None):
    from accounts.forms import CustomerForm
    obj = get_object_or_404(Customer, pk=pk) if pk else None
    form = CustomerForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer saved.")
        return redirect("core:customers")
    ctx = {"form": form, "obj": obj}
    if obj:
        ctx["subs"] = obj.subscriptions.select_related("plan")[:20]
        ctx["invoices"] = obj.invoices.all()[:10]
        ctx["owed"] = obj.invoices.filter(state=Invoice.CONFIRMED).aggregate(t=Sum(reports.BALANCE))["t"] or Decimal(0)
    return render(request, "core/customer_form.html", ctx)


# ---- customer portal: every query is scoped to the signed-in customer ----
def _mine(request):
    return request.user.customer


@customer_required
def portal(request):
    c = _mine(request)
    subs = c.subscriptions.exclude(state=Subscription.DRAFT).select_related("plan")
    invs = c.invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED])
    owed = invs.filter(state=Invoice.CONFIRMED).aggregate(t=Sum(reports.BALANCE))["t"] or Decimal(0)
    return render(request, "portal/home.html", {
        "subs": subs.filter(state__in=["active", "paused", "confirmed", "quotation"])[:6], "invoices": invs[:6], "owed": owed,
        "active": subs.filter(state="active").count(), "open_count": invs.filter(state=Invoice.CONFIRMED).count(),
        "overdue": [i for i in invs.filter(state=Invoice.CONFIRMED, due_date__lt=timezone.localdate())]})


@customer_required
def portal_subscriptions(request):
    qs = _mine(request).subscriptions.exclude(state=Subscription.DRAFT).select_related("plan")
    return render(request, "portal/subscriptions.html", {"page": paginate(request, qs)})


@customer_required
def portal_subscription(request, pk):
    sub = get_object_or_404(_mine(request).subscriptions.exclude(state=Subscription.DRAFT).select_related("plan"), pk=pk)
    return render(request, "portal/subscription.html", {
        "sub": sub, "lines": sub.lines.select_related("product", "variant", "tax"), "stages": Subscription.FLOW,
        "invoices": sub.invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED])[:12],
        "addons": sub.plan.products.filter(type=Product.ADDON, active=True),
        "enabled_addons": set(sub.lines.filter(product__type=Product.ADDON).values_list("product_id", flat=True))})


@customer_required
@require_POST
def portal_action(request, pk, action):
    sub = get_object_or_404(_mine(request).subscriptions.exclude(state=Subscription.DRAFT), pk=pk)
    allowed = {"confirm": services.confirm, "pause": services.pause, "resume": services.resume, "close": services.close}
    try:
        if action == "renew":
            new = services.renew(sub)
            messages.success(request, f"Renewed. Your new term {new.number} starts {new.start_date:%d %b %Y}.")
            return redirect("core:portal_subscription", pk=new.pk)
        if action in ("addon_on", "addon_off"):
            services.set_addon(sub, request.POST.get("product"), action == "addon_on")
            messages.success(request, "Add-on updated. It applies from your next invoice.")
        elif action in allowed:
            allowed[action](sub)
            messages.success(request, "Done.")
        else:
            messages.error(request, "That action isn't available.")
    except services.TransitionError as e:
        messages.error(request, str(e))
    return redirect("core:portal_subscription", pk=pk)


def _my_invoices(request):
    return _mine(request).invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED])


@customer_required
def portal_invoices(request):
    return render(request, "portal/invoices.html", {"page": paginate(request, _my_invoices(request).select_related("subscription"))})


@customer_required
def portal_invoice(request, pk):
    inv = get_object_or_404(_my_invoices(request), pk=pk)
    return render(request, "portal/invoice.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all()})


@customer_required
def portal_invoice_print(request, pk):
    inv = get_object_or_404(_my_invoices(request), pk=pk)
    return render(request, "billing/print.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all()})
