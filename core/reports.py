"""Finance and subscription metrics. Each figure has one definition, used by the dashboard, reports and exports.

 cash received  = succeeded, non-test payments dated in range (refunds are negative payments and net off)
 invoiced       = non-cancelled documents issued in range (credit notes are negative)
 outstanding    = unpaid balance on confirmed invoices, as of today
 overdue        = outstanding on invoices past their due date
 MRR            = sum of effective-dated contract changes up to the date (recurring lines, normalised to a month)
 movements      = new / expansion / reactivation / contraction / paused / churn / renewal(net zero)
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from billing.models import Invoice, InvoiceLine, Payment
from catalog.models import Plan
from subscriptions.models import Subscription, SubscriptionChange, SubscriptionLine

from . import reports_cache

D0 = Decimal("0")
BALANCE = ExpressionWrapper(F("total") - F("credited_total") - F("amount_paid"), output_field=DecimalField(max_digits=14, decimal_places=2))
RANGES = [("month", "This month"), ("30d", "Last 30 days"), ("quarter", "This quarter"), ("year", "This year")]


def parse_date(s, default=None):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


def range_dates(key, today=None):
    today = today or timezone.localdate()
    if key == "30d":
        return today - timedelta(days=29), today
    if key == "quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    if key == "year":
        return date(today.year, 1, 1), today
    return today.replace(day=1), today


def month_start(d, back=0):
    """First day of the month `back` months before d (negative goes forward)."""
    idx = d.year * 12 + d.month - 1 - back
    return date(idx // 12, idx % 12 + 1, 1)


def month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


class Filters:
    """plan, customer, salesperson, product ids from the query string. `key` makes cache entries distinct."""

    def __init__(self, plan=None, customer=None, salesperson=None, product=None, status=None):
        def identifier(value):
            value = str(value or "")
            return value if value.isascii() and value.isdecimal() and len(value) <= 19 and 0 < int(value) <= 9223372036854775807 else ""
        self.plan, self.customer, self.salesperson, self.product = map(identifier, (plan, customer, salesperson, product))
        self.status = status if status in dict(Subscription.STATES) else ""

    @classmethod
    def from_request(cls, g):
        return cls(g.get("plan"), g.get("customer"), g.get("salesperson"), g.get("product"), g.get("status"))

    @property
    def key(self):
        return "|".join([self.plan, self.customer, self.salesperson, self.product, self.status])

    @property
    def any(self):
        return bool(self.key.replace("|", ""))

    def subs(self):
        qs = Subscription.objects.all()
        if self.plan:
            qs = qs.filter(plan_id=self.plan)
        if self.customer:
            qs = qs.filter(customer_id=self.customer)
        if self.salesperson:
            qs = qs.filter(salesperson_id=self.salesperson)
        if self.product:
            qs = qs.filter(lines__product_id=self.product).distinct()
        if self.status:
            qs = qs.filter(state=self.status)
        return qs

    def changes(self):
        qs = SubscriptionChange.objects.all()
        return qs.filter(subscription__in=self.subs().values("pk")) if self.any else qs

    def invoices(self, qs=None):
        qs = Invoice.objects.all() if qs is None else qs
        if self.plan:
            qs = qs.filter(subscription__plan_id=self.plan)
        if self.customer:
            qs = qs.filter(customer_id=self.customer)
        if self.salesperson:
            qs = qs.filter(subscription__salesperson_id=self.salesperson)
        if self.product:
            qs = qs.filter(lines__product_id=self.product).distinct()
        return qs

    def payments(self, qs):
        if self.plan:
            qs = qs.filter(invoice__subscription__plan_id=self.plan)
        if self.customer:
            qs = qs.filter(invoice__customer_id=self.customer)
        if self.salesperson:
            qs = qs.filter(invoice__subscription__salesperson_id=self.salesperson)
        if self.product:
            qs = qs.filter(invoice__lines__product_id=self.product).distinct()
        return qs


NOFILTER = Filters()


# ---------------------------------------------------------------- recurring revenue (movement based)
def _movements(f):
    """[(effective_date, movement, delta, subscription_id)] for filtered changes with a non-zero delta. One query."""
    def load():
        rows = f.changes().exclude(mrr_before=F("mrr_after")).values_list("effective_date", "movement", "mrr_before", "mrr_after", "subscription_id")
        return [(d, m, a - b, s) for d, m, b, a, s in rows]
    return reports_cache.cached("movements", load, ttl=120, extra=f.key)


def mrr_at(day, f=NOFILTER):
    return sum((delta for d, _, delta, _ in _movements(f) if d <= day), D0)


def mrr_series(months=12, f=NOFILTER, today=None):
    """Month-end MRR plus the movement bridge for each of the last `months` months."""
    today = today or timezone.localdate()

    def build():
        mv = _movements(f)
        first = month_start(today, months - 1)
        opening = sum((x[2] for x in mv if x[0] < first), D0)
        by_month = defaultdict(lambda: defaultdict(lambda: [D0, 0]))
        for d, m, delta, sid in mv:
            if d >= first:
                cell = by_month[(d.year, d.month)][m]
                cell[0] += delta
                cell[1] += 1
        out, run = [], opening
        for i in range(months - 1, -1, -1):
            ms = month_start(today, i)
            cells = by_month[(ms.year, ms.month)]
            net = sum((c[0] for c in cells.values()), D0)
            row = {"label": ms.strftime("%b"), "month": ms, "opening": run}
            for k in ("new", "expansion", "reactivation", "contraction", "pause", "churn", "renewal"):
                row[k] = cells[k][0] if k in cells else D0
                row[k + "_n"] = cells[k][1] if k in cells else 0
            run += net
            row.update(closing=run, net=net, mrr=run)
            out.append(row)
        return out
    return reports_cache.cached("mrr_series", build, extra=f"{months}:{f.key}:{today}")


def bridge(start, end, f=NOFILTER):
    """MRR bridge for an arbitrary range, plus logo and revenue churn."""
    mv = _movements(f)
    opening = sum((x[2] for x in mv if x[0] < start), D0)
    cells = defaultdict(lambda: [D0, 0])
    for d, m, delta, _ in mv:
        if start <= d <= end:
            cells[m][0] += delta
            cells[m][1] += 1
    closing = opening + sum((c[0] for c in cells.values()), D0)
    # Churn rates are measured against the base that existed at the start of the period, so contracts that
    # joined and left inside the same period can't push the rate above 100%.
    started = {sid for d, m, _, sid in mv if m == "new" and d < start}
    gone_before = {sid for d, m, _, sid in mv if m == "churn" and d < start}
    base = started - gone_before
    churned_base = [(d, delta) for d, m, delta, sid in mv if m == "churn" and start <= d <= end and sid in base]
    churn_mrr = abs(sum((x[1] for x in churned_base), D0))
    churned_n = cells["churn"][1]
    return {"opening": opening, "closing": closing, "cells": {k: v for k, v in cells.items()}, "net": closing - opening,
            "arr": closing * 12, "churned_n": len(churned_base), "churned_all": churned_n, "new_n": cells["new"][1], "active_at_start": len(base),
            "logo_churn": min(Decimal(len(churned_base)) / len(base) * 100, Decimal(100)) if base else D0,
            "revenue_churn": min(churn_mrr / opening * 100, Decimal(100)) if opening else D0,
            "expansion_rate": (cells["expansion"][0] / opening * 100) if opening else D0}


# ---------------------------------------------------------------- cash / invoicing
def summary(start=None, end=None, f=NOFILTER):
    today = timezone.localdate()
    start, end = start or today.replace(day=1), end or today
    ok_pay = Payment.objects.filter(status=Payment.SUCCEEDED, date__gte=start, date__lte=end)
    pays = f.payments(ok_pay.filter(is_test=False))
    test = f.payments(ok_pay.filter(is_test=True))
    invs = f.invoices(Invoice.objects.exclude(state=Invoice.CANCELLED).filter(issue_date__gte=start, issue_date__lte=end))
    open_ = f.invoices(Invoice.objects.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note"))
    overdue = open_.filter(due_date__lt=today)
    subs = f.subs()
    live = subs.filter(state=Subscription.ACTIVE, start_date__lte=today)
    agg = lambda qs, expr: qs.aggregate(t=Sum(expr))["t"] or D0
    mrr = mrr_at(min(end, today), f)
    return {
        "start": start, "end": end,
        "active": live.count(), "paused": subs.filter(state=Subscription.PAUSED).count(),
        "cash": agg(pays, "amount"), "payments": pays.filter(amount__gt=0).count(),
        "test_cash": agg(test, "amount"), "test_count": test.count(),
        "refunds": -agg(pays.filter(amount__lt=0), "amount"),
        "invoiced": agg(invs, "total"), "invoice_count": invs.exclude(kind="credit_note").count(),
        "credited": -agg(invs.filter(kind="credit_note"), "total"),
        "outstanding": agg(open_, BALANCE), "outstanding_count": open_.count(),
        "overdue": agg(overdue, BALANCE), "overdue_count": overdue.count(),
        "pending_count": Payment.objects.filter(status=Payment.PENDING).count(),
        "by_method": list(pays.values("method").annotate(t=Sum("amount")).order_by("-t")),
        "mrr_rows": mrr_by_plan(f), "mrr": mrr, "arr": mrr * 12,
    }


def mrr_by_plan(f=NOFILTER):
    active = f.subs().filter(state=Subscription.ACTIVE, start_date__lte=timezone.localdate())
    rows = (SubscriptionLine.objects.filter(subscription__in=active.values("pk"), recurring=True)
            .values("subscription__plan__name", "subscription__plan__interval")
            .annotate(total=Sum(ExpressionWrapper(F("unit_price") * F("quantity"), output_field=DecimalField(max_digits=14, decimal_places=2))),
                      subs=Count("subscription_id", distinct=True)))
    out = []
    for r in rows:
        out.append({"plan": r["subscription__plan__name"], "interval": r["subscription__plan__interval"], "subs": r["subs"],
                    "mrr": (r["total"] or D0) * Plan.MONTHLY_FACTOR[r["subscription__plan__interval"]]})
    return sorted(out, key=lambda x: -x["mrr"])


def month_series(n=12, f=NOFILTER, today=None):
    """Invoiced vs cash received per month."""
    today = today or timezone.localdate()

    def build():
        from django.db.models.functions import TruncMonth
        first = month_start(today, n - 1)
        inv, pay = defaultdict(lambda: D0), defaultdict(lambda: D0)
        for m, t in (f.invoices(Invoice.objects.exclude(state=Invoice.CANCELLED).filter(issue_date__gte=first))
                     .annotate(m=TruncMonth("issue_date")).values_list("m").annotate(t=Sum("total"))):
            inv[(m.year, m.month)] = t
        for m, t in (f.payments(Payment.objects.filter(status=Payment.SUCCEEDED, is_test=False, date__gte=first))
                     .annotate(m=TruncMonth("date")).values_list("m").annotate(t=Sum("amount"))):
            pay[(m.year, m.month)] = t
        out = []
        for i in range(n - 1, -1, -1):
            ms = month_start(today, i)
            out.append({"label": ms.strftime("%b"), "invoiced": inv[(ms.year, ms.month)], "collected": pay[(ms.year, ms.month)]})
        return out
    return reports_cache.cached("month_series", build, extra=f"{n}:{f.key}:{today}")


def week_bars(today=None):
    today = today or timezone.localdate()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    got = defaultdict(lambda: D0)
    for d, t in Payment.objects.filter(status=Payment.SUCCEEDED, is_test=False, date__gte=days[0]).values_list("date", "amount"):
        got[d] += t
    return [{"day": f"{d:%a}", "value": got[d], "today": d == today} for d in days]


def aging(f=NOFILTER):
    """Outstanding balance of confirmed invoices by days past due."""
    today = timezone.localdate()
    open_ = f.invoices(Invoice.objects.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note")).annotate(bal=BALANCE)
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
        a = qs.aggregate(t=Sum("bal"), n=Count("id"))
        out.append({"label": label, "amount": a["t"] or D0, "count": a["n"]})
        total += a["t"] or D0
    for b in out:
        b["pct"] = int(b["amount"] / total * 100) if total else 0
    return out, total


def top_customers(limit=8, f=NOFILTER):
    qs = f.invoices(Invoice.objects.exclude(state=Invoice.CANCELLED))
    return list(qs.values("customer_id", "bill_to_name").annotate(billed=Sum("total"), paid=Sum("amount_paid")).order_by("-billed")[:limit])


def revenue_by(dim, start, end, f=NOFILTER, limit=15):
    """Invoiced net revenue (after discounts, before tax) grouped by plan / product / customer / salesperson / month."""
    lines = InvoiceLine.objects.filter(invoice__issue_date__gte=start, invoice__issue_date__lte=end).exclude(invoice__state=Invoice.CANCELLED)
    if f.plan:
        lines = lines.filter(invoice__subscription__plan_id=f.plan)
    if f.customer:
        lines = lines.filter(invoice__customer_id=f.customer)
    if f.salesperson:
        lines = lines.filter(invoice__subscription__salesperson_id=f.salesperson)
    if f.product:
        lines = lines.filter(product_id=f.product)
    key = {"plan": "invoice__subscription__plan__name", "product": "product__name", "customer": "invoice__bill_to_name",
           "salesperson": "invoice__subscription__salesperson__name", "month": "invoice__issue_date__month"}[dim]
    rows = lines.values(key).annotate(net=Sum("amount"), n=Count("invoice_id", distinct=True)).order_by("-net")[:limit]
    return [{"label": r[key] if r[key] not in (None, "") else "—", "net": r["net"] or D0, "invoices": r["n"]} for r in rows]


def failed_payment_trend(n=6, today=None):
    today = today or timezone.localdate()
    first = month_start(today, n - 1)
    cnt = defaultdict(int)
    for d in Payment.objects.filter(status=Payment.FAILED, created_at__date__gte=first).values_list("created_at", flat=True):
        cnt[(d.year, d.month)] += 1
    return [{"label": month_start(today, i).strftime("%b"), "n": cnt[(month_start(today, i).year, month_start(today, i).month)]} for i in range(n - 1, -1, -1)]


def renewal_forecast(days=120, f=NOFILTER):
    """Expiring contracts by month with their MRR, split by renewal policy."""
    today = timezone.localdate()
    subs = f.subs().filter(state=Subscription.ACTIVE, expiry_date__gte=today, expiry_date__lte=today + timedelta(days=days)).prefetch_related("lines").select_related("plan")
    out = defaultdict(lambda: {"n": 0, "mrr": D0, "auto": 0, "manual": 0})
    for s in subs:
        cell = out[(s.expiry_date.year, s.expiry_date.month)]
        cell["n"] += 1
        cell["mrr"] += s.recurring_monthly
        cell["auto" if s.renewal_policy == "auto" else "manual"] += 1
    return [{"label": date(y, m, 1).strftime("%b %Y"), **v} for (y, m), v in sorted(out.items())]


def cohorts(months=12, f=NOFILTER, today=None):
    """Customer retention by first-activation month: share of each cohort still holding an active contract k months later."""
    today = today or timezone.localdate()

    def build():
        rows = f.changes().filter(kind__in=["activated", "closed", "renewed"]).values_list("subscription__customer_id", "subscription_id", "kind", "effective_date", "movement")
        per_sub = defaultdict(dict)
        cust_of = {}
        for cid, sid, kind, eff, mov in rows:
            cust_of[sid] = cid
            if kind in ("activated", "renewed"):
                per_sub[sid]["start"] = min(per_sub[sid].get("start", eff), eff)
            elif kind == "closed":
                per_sub[sid]["end"] = eff
        cust_spans = defaultdict(list)
        for sid, v in per_sub.items():
            if "start" in v:
                cust_spans[cust_of[sid]].append((v["start"], v.get("end")))
        cohorts_ = defaultdict(list)
        for cid, sp in cust_spans.items():
            first = min(s for s, _ in sp)
            cohorts_[month_start(first)].append(sp)
        out = []
        for i in range(months - 1, -1, -1):
            ms = month_start(today, i)
            group = cohorts_.get(ms, [])
            cells = []
            for k in range(0, i + 1):
                at = min(month_end(month_start(ms, -k)), today)
                alive = sum(1 for sp in group if any(s <= at and (e is None or e > at) for s, e in sp))
                cells.append({"k": k, "alive": alive, "pct": int(alive / len(group) * 100) if group else None})
            out.append({"label": ms.strftime("%b %Y"), "size": len(group), "cells": cells})
        return out
    return reports_cache.cached("cohorts", build, extra=f"{months}:{f.key}:{today}")


def billing_calendar(today=None):
    import calendar as cal
    today = today or timezone.localdate()
    first = today.replace(day=1)
    days = cal.monthrange(today.year, today.month)[1]
    last = today.replace(day=days)
    issued = dict(Invoice.objects.filter(issue_date__gte=first, issue_date__lte=last).exclude(kind="credit_note").values_list("issue_date").annotate(n=Count("id")))
    upcoming = dict(Subscription.objects.filter(state=Subscription.ACTIVE, next_invoice_date__gte=first, next_invoice_date__lte=last).values_list("next_invoice_date").annotate(n=Count("id")))
    cells = [{"blank": True} for _ in range(first.weekday())]
    for d in range(1, days + 1):
        day = today.replace(day=d)
        cells.append({"d": d, "issued": issued.get(day, 0), "due": upcoming.get(day, 0), "today": day == today, "past": day < today})
    return {"cells": cells, "month": first, "issued": sum(issued.values()), "due": sum(upcoming.values())}


def activity_feed(limit=14):
    inv = [{"at": i.created_at, "day": i.issue_date, "kind": "invoice", "obj": i, "title": i.bill_to_name, "ref": i.number,
            "amount": i.total, "state": "credit" if i.is_credit_note else i.display_state}
           for i in Invoice.objects.select_related("subscription").order_by("-id")[:limit]]
    pay = [{"at": p.created_at, "day": p.date, "kind": "payment", "obj": p.invoice, "title": p.invoice.bill_to_name, "ref": p.invoice.number,
            "amount": p.amount, "state": {"succeeded": "paid", "pending": "pending", "failed": "failed"}[p.status], "method": p.get_method_display()}
           for p in Payment.objects.select_related("invoice").order_by("-id")[:limit]]
    return sorted(inv + pay, key=lambda x: x["at"], reverse=True)[:limit]


def group_by_day(rows, key="day"):
    today = timezone.localdate()
    out, idx = [], {}
    for r in rows:
        d = r[key] if isinstance(r, dict) else getattr(r, key)
        if d not in idx:
            label = "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else d.strftime("%d %b %Y")
            idx[d] = {"label": label, "date": d, "items": [], "total": D0}
            out.append(idx[d])
        idx[d]["items"].append(r)
        amt = r["amount"] if isinstance(r, dict) else getattr(r, "total", getattr(r, "amount", D0))
        idx[d]["total"] += amt or D0
    return out
