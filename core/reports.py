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


def aging():
    """Outstanding balance of confirmed invoices by days past due."""
    today = timezone.localdate()
    open_ = Invoice.objects.filter(state=Invoice.CONFIRMED).annotate(bal=BALANCE)
    buckets = [("Not yet due", None, 0), ("1–30 days", 1, 30), ("31–60 days", 31, 60), ("61–90 days", 61, 90), ("90+ days", 91, None)]
    out, total = [], D0
    for label, lo, hi in buckets:
        qs = open_
        if lo is None:
            qs = qs.filter(due_date__gte=today)
        else:
            qs = qs.filter(due_date__lte=today - timedelta(days=lo))
            if hi is not None:
                qs = qs.filter(due_date__gte=today - timedelta(days=hi))
        agg = qs.aggregate(t=Sum("bal"), n=Count("id"))
        out.append({"label": label, "amount": agg["t"] or D0, "count": agg["n"]})
        total += agg["t"] or D0
    for b in out:
        b["pct"] = int(b["amount"] / total * 100) if total else 0
    return out, total


def mrr_trend(n=12):
    """MRR at each month end from subscription lifetimes, valued at current line prices (approximation: price history is not versioned)."""
    from django.db.models import Subquery, OuterRef
    today = timezone.localdate()
    subs = list(Subscription.objects.filter(activated_at__isnull=False).values("id", "start_date", "closed_at", "plan__interval")
                .annotate(sub_total=Sum(ExpressionWrapper(F("lines__unit_price") * F("lines__quantity"), output_field=DecimalField(max_digits=14, decimal_places=2)))))
    months, y, m = [], today.year, today.month
    for _ in range(n):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    out, prev = [], D0
    for (y, m) in months:
        end = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        end = min(end, today)
        mrr, new, churned = D0, 0, 0
        for s in subs:
            a = s["start_date"]
            c = s["closed_at"].astimezone().date() if s["closed_at"] else None
            if a <= end and (c is None or c > end):
                mrr += (s["sub_total"] or D0) * Plan.MONTHLY_FACTOR[s["plan__interval"]]
            if a.year == y and a.month == m:
                new += 1
            if c and c.year == y and c.month == m:
                churned += 1
        out.append({"label": date(y, m, 1).strftime("%b"), "mrr": mrr, "new": new, "churned": churned, "delta": mrr - prev})
        prev = mrr
    return out


def top_customers(limit=8):
    return list(Invoice.objects.exclude(state=Invoice.CANCELLED).values("customer_id", "bill_to_name")
                .annotate(billed=Sum("total"), paid=Sum("amount_paid")).order_by("-billed")[:limit])


def single_line(points, w=640, h=200, pad=(8, 12, 24, 8), key="mrr"):
    left, top, bottom, right = pad
    peak = max([p[key] for p in points] + [Decimal(1)])
    step = (w - left - right) / max(len(points) - 1, 1)
    pts = [(left + i * step, top + (h - top - bottom) * (1 - float(p[key] / peak))) for i, p in enumerate(points)]
    return {"w": w, "h": h, "path": smooth_path(pts), "area": smooth_path(pts) + f" L{pts[-1][0]:.1f},{h - bottom} L{pts[0][0]:.1f},{h - bottom} Z",
            "dots": [{"x": x, "y": y, **p} for (x, y), p in zip(pts, points)], "base": h - bottom,
            "labels": [{"x": x, "t": p["label"]} for (x, _), p in zip(pts, points)]}


def billing_calendar(today=None):
    """Month grid of invoices due for billing / issued per day, for the heatmap."""
    import calendar as cal
    today = today or timezone.localdate()
    first = today.replace(day=1)
    days = cal.monthrange(today.year, today.month)[1]
    issued = dict(Invoice.objects.filter(issue_date__gte=first, issue_date__lte=today.replace(day=days)).values_list("issue_date")
                  .annotate(n=Count("id")))
    upcoming = dict(Subscription.objects.filter(state=Subscription.ACTIVE, next_invoice_date__gte=first,
                                                next_invoice_date__lte=today.replace(day=days)).values_list("next_invoice_date").annotate(n=Count("id")))
    cells = [{"blank": True} for _ in range(first.weekday())]
    for d in range(1, days + 1):
        day = today.replace(day=d)
        cells.append({"d": d, "issued": issued.get(day, 0), "due": upcoming.get(day, 0), "today": day == today, "past": day < today})
    return {"cells": cells, "month": first, "issued": sum(issued.values()), "due": sum(upcoming.values())}


def activity_feed(limit=14):
    """Recent invoices and payments merged, newest first (for grouped-by-day history)."""
    inv = [{"at": i.created_at, "day": i.issue_date, "kind": "invoice", "obj": i, "title": i.bill_to_name, "ref": i.number,
            "amount": i.total, "state": i.display_state} for i in Invoice.objects.select_related("subscription").order_by("-id")[:limit]]
    pay = [{"at": p.created_at, "day": p.date, "kind": "payment", "obj": p.invoice, "title": p.invoice.bill_to_name, "ref": p.invoice.number,
            "amount": p.amount, "state": "paid", "method": p.get_method_display()}
           for p in Payment.objects.select_related("invoice").order_by("-id")[:limit]]
    return sorted(inv + pay, key=lambda x: x["at"], reverse=True)[:limit]


def group_by_day(rows, key="day"):
    """[{label, date, items, total}] preserving order; label is Today / Yesterday / '12 Oct'."""
    today = timezone.localdate()
    out, idx = [], {}
    for r in rows:
        d = r[key] if isinstance(r, dict) else getattr(r, key)
        if d not in idx:
            label = "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else d.strftime("%d %b %Y")
            idx[d] = {"label": label, "date": d, "items": [], "total": D0}
            out.append(idx[d])
        idx[d]["items"].append(r)
        amt = r["amount"] if isinstance(r, dict) else getattr(r, "total", D0)
        idx[d]["total"] += amt or D0
    return out


RANGES = [("month", "This month"), ("30d", "Last 30 days"), ("quarter", "This quarter"), ("year", "This year")]


def range_dates(key, today=None):
    today = today or timezone.localdate()
    if key == "30d":
        return today - timedelta(days=29), today
    if key == "quarter":
        m = 3 * ((today.month - 1) // 3) + 1
        return date(today.year, m, 1), today
    if key == "year":
        return date(today.year, 1, 1), today
    return today.replace(day=1), today
