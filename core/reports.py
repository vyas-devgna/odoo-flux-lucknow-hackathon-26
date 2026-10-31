"""Finance numbers. Each figure has one definition, used by the dashboard, the reports page and exports.

 cash received  = sum of payments dated in the range
 invoiced       = total of non-cancelled invoices issued in the range
 outstanding    = unpaid balance on confirmed invoices (all time, as of today)
 overdue        = outstanding on confirmed invoices past their due date
 MRR            = recurring subtotal of active subscriptions normalised to a month
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from billing.models import Invoice, Payment
from catalog.models import Plan
from subscriptions.models import Subscription, SubscriptionLine

D0 = Decimal("0")
LINE_TOTAL = ExpressionWrapper(F("unit_price") * F("quantity"), output_field=DecimalField(max_digits=14, decimal_places=2))
BALANCE = ExpressionWrapper(F("total") - F("amount_paid"), output_field=DecimalField(max_digits=14, decimal_places=2))


def parse_date(s, default=None):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


def mrr_by_plan(plan=None):
    active = Subscription.objects.filter(state=Subscription.ACTIVE)
    if plan:
        active = active.filter(plan_id=plan)
    rows = (SubscriptionLine.objects.filter(subscription__in=active)
            .values("subscription__plan_id", "subscription__plan__name", "subscription__plan__interval")
            .annotate(total=Sum(LINE_TOTAL)))
    subs = dict(active.values_list("plan_id").annotate(n=Count("id")))
    out = []
    for r in rows:
        factor = Plan.MONTHLY_FACTOR[r["subscription__plan__interval"]]
        out.append({"plan": r["subscription__plan__name"], "interval": r["subscription__plan__interval"],
                    "subs": subs.get(r["subscription__plan_id"], 0), "mrr": (r["total"] or D0) * factor})
    return sorted(out, key=lambda x: -x["mrr"])


def smooth_path(points):
    """Catmull-Rom -> cubic Bezier path through (x, y) points."""
    if len(points) < 2:
        return ""
    d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = points[max(i - 1, 0)], points[i], points[i + 1], points[min(i + 2, len(points) - 1)]
        lo, hi = min(p1[1], p2[1]), max(p1[1], p2[1])  # keep control points between neighbours: no overshoot below zero
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, min(max(p1[1] + (p2[1] - p0[1]) / 6, lo), hi))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, min(max(p2[1] - (p3[1] - p1[1]) / 6, lo), hi))
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d


def month_series(n=12):
    today = timezone.localdate()
    first = date(today.year, today.month, 1)
    months = []
    y, m = first.year, first.month
    for _ in range(n):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    inv = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["t"] for r in
           Invoice.objects.exclude(state=Invoice.CANCELLED).filter(issue_date__gte=months[0])
           .annotate(m=TruncMonth("issue_date")).values("m").annotate(t=Sum("total"))}
    pay = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["t"] for r in
           Payment.objects.filter(date__gte=months[0]).annotate(m=TruncMonth("date")).values("m").annotate(t=Sum("amount"))}
    return [{"label": f"{m:%b}", "invoiced": inv.get(m, D0), "collected": pay.get(m, D0)} for m in months]


def chart(series, w=640, h=220, pad=(36, 12, 28, 12)):
    left, top, bottom, right = pad
    peak = max([s["invoiced"] for s in series] + [s["collected"] for s in series] + [Decimal(1)])
    step = (w - left - right) / max(len(series) - 1, 1)

    def pts(key):
        return [(left + i * step, top + (h - top - bottom) * (1 - float(s[key] / peak))) for i, s in enumerate(series)]
    a, b = pts("invoiced"), pts("collected")
    grid = [{"y": top + (h - top - bottom) * (1 - t), "label": peak * Decimal(str(t))} for t in (0, 0.5, 1)]
    return {"w": w, "h": h, "invoiced": smooth_path(a), "collected": smooth_path(b), "grid": grid,
            "labels": [{"x": left + i * step, "t": s["label"]} for i, s in enumerate(series)],
            "dots": [{"x": x, "y": y, "inv": s["invoiced"], "col": s["collected"], "label": s["label"]}
                     for (x, y), s in zip(b, series)], "left": left, "right": w - right, "base": h - bottom}


def week_bars():
    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    got = dict(Payment.objects.filter(date__gte=days[0]).values_list("date").annotate(t=Sum("amount")))
    vals = [got.get(d, D0) for d in days]
    peak = max(vals + [Decimal(1)])
    return [{"day": f"{d:%a}", "value": v, "pct": int(v / peak * 100), "today": d == today} for d, v in zip(days, vals)]


def summary(start=None, end=None, plan=None):
    today = timezone.localdate()
    start = start or today.replace(day=1)
    end = end or today
    pays = Payment.objects.filter(date__gte=start, date__lte=end)
    invs = Invoice.objects.exclude(state=Invoice.CANCELLED).filter(issue_date__gte=start, issue_date__lte=end)
    open_ = Invoice.objects.filter(state=Invoice.CONFIRMED)
    subs = Subscription.objects.all()
    if plan:
        pays, invs, open_, subs = (pays.filter(invoice__subscription__plan_id=plan), invs.filter(subscription__plan_id=plan),
                                   open_.filter(subscription__plan_id=plan), subs.filter(plan_id=plan))
    overdue = open_.filter(due_date__lt=today)
    agg = lambda qs, expr: qs.aggregate(t=Sum(expr))["t"] or D0
    mrr = mrr_by_plan(plan)
    return {
        "start": start, "end": end,
        "active": subs.filter(state=Subscription.ACTIVE).count(),
        "paused": subs.filter(state=Subscription.PAUSED).count(),
        "cash": agg(pays, "amount"), "payments": pays.count(),
        "invoiced": agg(invs, "total"), "invoice_count": invs.count(),
        "outstanding": agg(open_, BALANCE), "outstanding_count": open_.count(),
        "overdue": agg(overdue, BALANCE), "overdue_count": overdue.count(),
        "by_method": list(pays.values("method").annotate(t=Sum("amount")).order_by("-t")),
        "mrr_rows": mrr, "mrr": sum((r["mrr"] for r in mrr), D0),
    }
