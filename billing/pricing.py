"""Pure invoice pricing: lines, discounts, tax. No writes. One implementation shared by billing runs, previews and first invoices."""
from django.db.models import F, Q
from django.utils import timezone

from catalog.models import Discount
from core.money import ZERO, q


def eligible_discount(sub, rows, on):
    """Pick the single best eligible rule (stacking is out of policy). Returns (rule, amount, {row_index: share})."""
    best = None
    rules = (Discount.objects.filter(active=True, start_date__lte=on)
             .filter(Q(end_date__isnull=True) | Q(end_date__gte=on))
             .filter(Q(usage_limit__isnull=True) | Q(times_used__lt=F("usage_limit")))
             .prefetch_related("products", "plans").order_by("id"))
    for rule in rules:
        plans = {p.pk for p in rule.plans.all()}
        if plans and sub.plan_id not in plans:
            continue
        if rule.scope == Discount.PRODUCTS:
            scope = {p.pk for p in rule.products.all()}
            if not scope:
                continue
            idx = [i for i, r in enumerate(rows) if r["product_id"] in scope]
        else:
            idx = list(range(len(rows)))
        base = sum((rows[i]["net"] for i in idx), ZERO)
        units = sum(rows[i]["quantity"] for i in idx)
        if base <= 0 or base < rule.min_purchase or units < rule.min_quantity:
            continue
        raw = rule.value if rule.kind == Discount.FIXED else base * rule.value / 100
        amount = min(q(raw), base)
        if amount > 0 and (best is None or amount > best[1]):
            best = (rule, amount, idx, base)
    if not best:
        return None, ZERO, {}
    rule, amount, idx, base = best
    alloc, left = {}, amount
    for n, i in enumerate(idx):  # deterministic: proportional shares, remainder on the last eligible line
        share = left if n == len(idx) - 1 else min(q(amount * rows[i]["net"] / base), left)
        alloc[i] = share
        left -= share
    return rule, amount, alloc


def price(sub, index, on, use_discount=True):
    """Return (rule, rows, subtotal, discount_total, tax_total). Non-recurring lines are billed on the first period only."""
    lines = [l for l in sub.lines.select_related("product", "variant", "tax") if l.recurring or index == 0]
    base_rows = [dict(product_id=l.product_id, quantity=l.quantity, net=l.net_amount, line=l) for l in lines]
    rule, d_total, alloc = eligible_discount(sub, base_rows, on) if use_discount else (None, ZERO, {})
    rows, subtotal, tax_total = [], ZERO, ZERO
    for i, r in enumerate(base_rows):
        l, disc = r["line"], alloc.get(i, ZERO)
        net_after = r["net"] - disc
        if l.inclusive and disc == 0:
            tax = l.tax_amount
        else:
            tax = q(net_after * l.tax_rate / 100)
        rows.append(dict(product_id=l.product_id, description=l.description, quantity=l.quantity, unit_price=l.unit_price, discount=disc,
                         tax_name=l.tax.name if l.tax_id else "", tax_rate=l.tax_rate, tax_amount=tax, amount=net_after))
        subtotal += r["net"]
        tax_total += tax
    return rule, rows, subtotal, d_total, tax_total
