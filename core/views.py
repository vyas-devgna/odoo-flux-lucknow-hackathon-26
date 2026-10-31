from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Customer
from billing.models import Invoice
from catalog.models import Plan, Product, Tax
from subscriptions import services
from subscriptions.models import Subscription

from . import reports
from . import audit
from .crud import paginate
from .permissions import customer_required, internal_required


def home(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    return redirect("core:dashboard" if request.user.is_internal else "core:portal")


@internal_required
def dashboard(request):
    today = timezone.localdate()
    rng = request.GET.get("range", "month")
    start, end = reports.range_dates(rng, today)
    s = reports.summary(start, end)
    rate = min(int(s["cash"] / s["invoiced"] * 100), 100) if s["invoiced"] else 0
    open_total = s["outstanding"]
    overdue_pct = min(int(s["overdue"] / open_total * 100), 100) if open_total else 0
    feed = reports.group_by_day(reports.activity_feed(16))
    open_invoices = Invoice.objects.filter(state=Invoice.CONFIRMED).order_by("due_date").select_related("subscription")[:60]
    renewals = (Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__lte=today + timedelta(days=45),
                                            expiry_date__gte=today).select_related("customer", "plan").order_by("expiry_date")[:5])
    empty = not Subscription.objects.exists()
    setup = [("Add a tax rule", "catalog:taxes_list", Tax.objects.exists()), ("Create a product", "catalog:products_list", Product.objects.exists()),
             ("Create a plan", "catalog:plans_list", Plan.objects.exists()), ("Add your first customer", "core:customer_new", Customer.objects.exists()),
             ("Quote a subscription", "subscriptions:new", Subscription.objects.exists())] if empty else []
    return render(request, "core/dashboard.html", {
        "s": s, "rng": rng, "ranges": reports.RANGES, "rate": rate, "overdue_pct": overdue_pct, "arr": s["mrr"] * 12,
        "feed": feed, "open_invoices": open_invoices, "cal": reports.billing_calendar(today), "renewals": renewals, "today": today,
        "drafts": Invoice.objects.filter(state=Invoice.DRAFT).count(), "setup": setup, "empty": empty,
        "due_now": Subscription.objects.filter(state="active", next_invoice_date__lte=today).count(),
        "quotes_open": Subscription.objects.filter(state="quotation").count(),
        "methods": [("bank", "Bank transfer"), ("upi", "UPI"), ("card", "Card"), ("cash", "Cash"), ("cheque", "Cheque")]})


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
    ag, ag_total = reports.aging()
    trend = reports.mrr_trend()
    cur, prev = trend[-1], trend[-2] if len(trend) > 1 else trend[-1]
    return render(request, "core/reports.html", {
        "aging": ag, "aging_total": ag_total, "trend": trend, "mrr_chart": reports.single_line(trend), "top": reports.top_customers(),
        "new_subs": sum(t["new"] for t in trend[-3:]), "churned": sum(t["churned"] for t in trend[-3:]), "mrr_delta": cur["mrr"] - prev["mrr"],
        "net_new": sum(t["new"] - t["churned"] for t in trend[-3:]), "arpa": (s["mrr"] / s["active"]) if s["active"] else 0, "chart": reports.chart(reports.month_series()),
        "s": s, "page": paginate(request, qs), "view": status,
        "views": [("overdue", "Overdue"), ("outstanding", "Outstanding"), ("paid", "Paid")], "plans": Plan.objects.all(), "plan": plan or "",
        "from_": s["start"].isoformat(), "to": s["end"].isoformat(), "today": timezone.localdate()})


# ---- customers (staff) ----
@internal_required
def customer_list(request):
    q = request.GET.get("q", "").strip()
    qs = Customer.objects.annotate(n_subs=Count("subscriptions", distinct=True)).order_by("name", "id")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q))
    return render(request, "core/customers.html", {"page": paginate(request, qs), "q": q})


@internal_required
def customer_form(request, pk=None):
    from accounts.forms import CustomerForm
    obj = get_object_or_404(Customer, pk=pk) if pk else None
    form = CustomerForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        audit.log(request.user, "customer.save", saved)
        messages.success(request, "Customer saved.")
        if request.GET.get("then") == "quote":
            return redirect(f"{reverse('subscriptions:new')}?customer={saved.pk}")
        return redirect("core:customers")
    ctx = {"form": form, "obj": obj}
    if obj:
        ctx["subs"] = obj.subscriptions.select_related("plan")[:20]
        ctx["invoices"] = obj.invoices.all()[:10]
        ctx["owed"] = obj.invoices.filter(state=Invoice.CONFIRMED).aggregate(t=Sum(reports.BALANCE))["t"] or Decimal(0)
        ctx["paid"] = obj.invoices.aggregate(t=Sum("amount_paid"))["t"] or Decimal(0)
        ctx["mrr"] = sum((s.recurring_monthly for s in obj.subscriptions.filter(state="active")), Decimal(0))
        ctx["timeline"] = audit.timeline(obj)
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
            audit.log(request.user, "portal.renew", sub, f"renewed as {new.number}")
            messages.success(request, f"Renewed. Your new term {new.number} starts {new.start_date:%d %b %Y}.")
            return redirect("core:portal_subscription", pk=new.pk)
        if action in ("addon_on", "addon_off"):
            services.set_addon(sub, request.POST.get("product"), action == "addon_on")
            audit.log(request.user, f"portal.{action}", sub, f"product {request.POST.get('product')}")
            messages.success(request, "Add-on updated. It applies from your next invoice.")
        elif action in allowed:
            allowed[action](sub)
            audit.log(request.user, f"portal.{action}", sub)
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


# ---- company settings, audit trail, search, exports, health ----
import csv

from django import forms as djforms
from django.db import connection
from django.http import HttpResponse, JsonResponse

from .crud import StyledModelForm
from .models import AuditLog, CompanySettings
from .permissions import admin_required


class CompanyForm(StyledModelForm):
    class Meta:
        model = CompanySettings
        exclude = []
        widgets = {"address": djforms.Textarea(attrs={"rows": 3}), "bank_details": djforms.Textarea(attrs={"rows": 3})}


@admin_required
def company_settings(request):
    form = CompanyForm(request.POST or None, instance=CompanySettings.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request.user, "settings.update", detail="company settings")
        messages.success(request, "Settings saved.")
        return redirect("core:settings")
    return render(request, "core/settings.html", {"form": form})


@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related("actor")
    q, action = request.GET.get("q", "").strip(), request.GET.get("action", "")
    if q:
        qs = qs.filter(Q(target_label__icontains=q) | Q(actor_label__icontains=q) | Q(detail__icontains=q))
    if action:
        qs = qs.filter(action__startswith=action)
    groups = ["subscription", "invoice", "payment", "billing", "account", "team", "settings", "portal", "discounts", "plans", "products"]
    return render(request, "core/audit.html", {"page": paginate(request, qs), "q": q, "action": action, "groups": groups})


@internal_required
def search(request):
    q = request.GET.get("q", "").strip()
    res = {}
    if len(q) >= 2:
        res["customers"] = Customer.objects.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q))[:8]
        res["subscriptions"] = Subscription.objects.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer__company__icontains=q)).select_related("customer", "plan")[:8]
        res["invoices"] = Invoice.objects.filter(Q(number__icontains=q) | Q(bill_to_name__icontains=q)).select_related("subscription")[:8]
    return render(request, "core/search.html", {"q": q, "res": res, "any": any(res.values())})


def _csv(name, header, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    w = csv.writer(resp)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return resp


@internal_required
def export(request, what):
    from billing.models import Payment
    if what == "subscriptions":
        qs = Subscription.objects.select_related("customer", "plan")
        if request.GET.get("state"):
            qs = qs.filter(state=request.GET["state"])
        return _csv("subscriptions", ["Number", "Customer", "Email", "Plan", "State", "Start", "Expiry", "Next invoice", "Monthly value"],
                    ([s.number, s.customer, s.customer.email, s.plan.name, s.state, s.start_date, s.expiry_date, s.next_invoice_date, s.recurring_monthly]
                     for s in qs.iterator()))
    if what == "payments":
        qs = Payment.objects.select_related("invoice")
        return _csv("payments", ["Date", "Invoice", "Customer", "Method", "Amount", "Note"],
                    ([p.date, p.invoice.number, p.invoice.bill_to_name, p.method, p.amount, p.note] for p in qs.iterator()))
    if what == "customers":
        return _csv("customers", ["Name", "Company", "Email", "Phone", "Tax ID"],
                    ([c.name, c.company, c.email, c.phone, c.tax_id] for c in Customer.objects.order_by("name").iterator()))
    return HttpResponse(status=404)


def healthz(request):
    try:
        with connection.cursor() as c:
            c.execute("select 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "db_error"}, status=503)


@customer_required
def portal_profile(request):
    from accounts.forms import CustomerForm
    c = _mine(request)
    form = CustomerForm(request.POST or None, instance=c)
    form.fields.pop("email")  # login email changes need verification; not self-service
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request.user, "portal.profile", c)
        messages.success(request, "Billing details updated. They apply to your next invoice.")
        return redirect("core:portal_profile")
    return render(request, "portal/profile.html", {"form": form})


@customer_required
def portal_pay(request, pk):
    from billing import services as billing
    inv = get_object_or_404(_my_invoices(request), pk=pk)
    co = CompanySettings.get()
    if inv.state != Invoice.CONFIRMED or inv.balance <= 0:
        messages.info(request, "This invoice has nothing left to pay.")
        return redirect("core:portal_invoice", pk=pk)
    if request.method == "POST":
        try:
            billing.pay_online(inv, request.user)
            messages.success(request, f"Payment received for {inv.number}. Thank you!")
        except billing.BillingError as e:
            messages.error(request, str(e))
        return redirect("core:portal_invoice", pk=pk)
    return render(request, "portal/pay.html", {"inv": inv, "enabled": co.online_payments, "lines": inv.lines.all()})


@internal_required
def customer_statement(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    invs = list(c.invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED]).order_by("issue_date", "id").prefetch_related("payments"))
    rows, bal = [], Decimal(0)
    for i in invs:
        bal += i.total
        rows.append({"date": i.issue_date, "ref": i.number, "desc": f"Invoice {i.period_start:%d %b}–{i.period_end:%d %b %Y}", "debit": i.total, "credit": None, "bal": bal})
        for p in i.payments.all():
            bal -= p.amount
            rows.append({"date": p.date, "ref": i.number, "desc": f"{'Refund' if p.amount < 0 else 'Payment'} · {p.get_method_display()}", "debit": -p.amount if p.amount < 0 else None, "credit": p.amount if p.amount > 0 else None, "bal": bal})
    rows.sort(key=lambda r: r["date"])
    run = Decimal(0)
    for r in rows:
        run += (r["debit"] or 0) - (r["credit"] or 0)
        r["bal"] = run
    return render(request, "core/statement.html", {"c": c, "rows": rows, "balance": run, "today": timezone.localdate()})
