import csv
from decimal import Decimal

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.models import Customer, User
from billing.models import Invoice
from catalog.models import Plan, Product
from subscriptions.models import Subscription

from . import reports
from .crud import paginate
from .permissions import cap_required

finance_required = cap_required("finance")


def _common(request, default_range="year"):
    g = request.GET
    f = reports.Filters.from_request(g)
    today = timezone.localdate()
    ds, de = reports.range_dates(default_range, today)
    start, end = reports.parse_date(g.get("from"), ds), reports.parse_date(g.get("to"), de)
    if end < start:
        start, end = end, start
    return f, start, end, {
        "f": f, "from_": start.isoformat(), "to": end.isoformat(), "start": start, "end": end,
        "plans": Plan.objects.all(), "products": Product.objects.filter(active=True), "salespeople": User.objects.filter(role__in=["admin", "staff"]),
        "customers": Customer.objects.order_by("name")[:300], "statuses": Subscription.STATES}


def _csv(name, header, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    return resp


@finance_required
def revenue(request):
    f, start, end, ctx = _common(request, "year")
    s = reports.summary(start, end, f)
    cash = reports.month_series(12, f)
    view = request.GET.get("view", "overdue")
    qs = f.invoices(Invoice.objects.select_related("subscription", "customer").exclude(kind="credit_note"))
    today = timezone.localdate()
    if view == "overdue":
        qs = qs.filter(state=Invoice.CONFIRMED, due_date__lt=today).order_by("due_date")
    elif view == "outstanding":
        qs = qs.filter(state=Invoice.CONFIRMED).order_by("due_date")
    else:
        qs = qs.filter(state=Invoice.PAID).order_by("-id")
    if request.GET.get("export") == "csv":
        return _csv("revenue-invoices", ["Number", "Customer", "Issued", "Due", "Total", "Balance", "Status"], ([i.number, i.bill_to_name, i.issue_date, i.due_date, i.total, i.balance, i.display_state] for i in qs))
    trend = reports.failed_payment_trend()
    ctx.update({"s": s, "view": view, "views": [("overdue", "Overdue"), ("outstanding", "Outstanding"), ("paid", "Paid")], "page": paginate(request, qs), "title": "Revenue",
                "cash_cfg": {"type": "bar", "labels": [r["label"] for r in cash], "datasets": [{"label": "Invoiced", "data": [float(r["invoiced"]) for r in cash], "color": "orange"}, {"label": "Collected", "data": [float(r["collected"]) for r in cash], "color": "accent"}]},
                "fail_trend": trend, "top": reports.top_customers(8, f)})
    return render(request, "core/reports/revenue.html", ctx)


@finance_required
def mrr(request):
    f, start, end, ctx = _common(request, "year")
    series = reports.mrr_series(12, f)
    br = reports.bridge(start, end, f)
    if request.GET.get("export") == "csv":
        return _csv("mrr-bridge", ["Month", "Opening", "New", "Expansion", "Reactivation", "Contraction", "Paused", "Churn", "Closing"],
                    ([r["label"], r["opening"], r["new"], r["expansion"], r["reactivation"], r["contraction"], r["pause"], r["churn"], r["closing"]] for r in series))
    cells = br["cells"]
    get = lambda k: cells.get(k, [Decimal(0), 0])
    ctx.update({"series": series, "br": br, "get": {k: get(k) for k in ("new", "expansion", "reactivation", "contraction", "pause", "churn", "renewal")},
                "title": "Recurring revenue", "mrr_by_plan": reports.mrr_by_plan(f),
                "mrr_cfg": {"type": "line", "labels": [r["label"] for r in series], "datasets": [{"label": "MRR", "data": [float(r["closing"]) for r in series], "color": "accent", "fill": True}]},
                "move_cfg": {"type": "bar", "stacked": True, "legend": True, "labels": [r["label"] for r in series], "datasets": [
                    {"label": "New", "data": [float(r["new"]) for r in series], "color": "green"}, {"label": "Expansion", "data": [float(r["expansion"] + r["reactivation"]) for r in series], "color": "accent"},
                    {"label": "Contraction", "data": [float(r["contraction"] + r["pause"]) for r in series], "color": "amber"}, {"label": "Churn", "data": [float(r["churn"]) for r in series], "color": "red"}]},
                "forecast": reports.renewal_forecast(180, f)})
    return render(request, "core/reports/mrr.html", ctx)


@finance_required
def retention(request):
    f, start, end, ctx = _common(request, "year")
    co = reports.cohorts(12, f)
    ctx.update({"cohorts": co, "title": "Retention", "max_k": max((len(c["cells"]) for c in co), default=0), "ks": range(max((len(c["cells"]) for c in co), default=0))})
    return render(request, "core/reports/retention.html", ctx)


@finance_required
def receivables(request):
    f, start, end, ctx = _common(request, "year")
    ag, total = reports.aging(f)
    qs = f.invoices(Invoice.objects.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note").select_related("customer")).order_by("due_date")
    if request.GET.get("export") == "csv":
        return _csv("receivables", ["Invoice", "Customer", "Due", "Days overdue", "Balance"], ([i.number, i.bill_to_name, i.due_date, i.days_overdue, i.balance] for i in qs))
    ctx.update({"aging": ag, "aging_total": total, "page": paginate(request, qs), "title": "Receivables", "today": timezone.localdate(),
                "chart_cfg": {"type": "bar", "labels": [b["label"] for b in ag], "datasets": [{"label": "Outstanding", "data": [float(b["amount"]) for b in ag], "color": "orange"}]}})
    return render(request, "core/reports/receivables.html", ctx)


@finance_required
def breakdown(request):
    f, start, end, ctx = _common(request, "year")
    dim = request.GET.get("by", "plan")
    if dim not in ("plan", "product", "customer", "salesperson"):
        dim = "plan"
    rows = reports.revenue_by(dim, start, end, f, limit=50)
    total = sum((r["net"] for r in rows), Decimal(0))
    if request.GET.get("export") == "csv":
        return _csv(f"revenue-by-{dim}", [dim.title(), "Net revenue", "Invoices"], ([r["label"], r["net"], r["invoices"]] for r in rows))
    ctx.update({"rows": rows, "total": total, "dim": dim, "dims": [("plan", "Plan"), ("product", "Product"), ("customer", "Customer"), ("salesperson", "Salesperson")], "title": "Breakdown",
                "chart_cfg": {"type": "bar", "labels": [r["label"] for r in rows[:10]], "datasets": [{"label": "Net revenue", "data": [float(r["net"]) for r in rows[:10]], "color": "accent"}]}})
    return render(request, "core/reports/breakdown.html", ctx)
