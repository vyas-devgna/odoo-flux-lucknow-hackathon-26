from decimal import Decimal

from django import template
from django.conf import settings

register = template.Library()


@register.filter
def money(value):
    if value is None or value == "":
        return "—"
    value = Decimal(value)
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    # Indian digit grouping: last three digits, then pairs
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    whole = ",".join(parts + [tail])
    return f"{sign}{settings.CURRENCY_SYMBOL}{whole}.{frac}"


@register.filter
def short_money(value):
    value = Decimal(value or 0)
    n = abs(value)
    if n >= 10_000_000:
        s = f"{n / 10_000_000:.2f}Cr"
    elif n >= 100_000:
        s = f"{n / 100_000:.2f}L"
    elif n >= 1000:
        s = f"{n / 1000:.1f}K"
    else:
        s = f"{n:.0f}"
    return f"{'-' if value < 0 else ''}{settings.CURRENCY_SYMBOL}{s}"


@register.filter
def getattr_path(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if callable(obj):
            obj = obj()
        if obj is None:
            return None
    return obj


@register.filter
def initials(name):
    bits = str(name or "?").split()
    return "".join(b[0] for b in bits[:2]).upper() or "?"


@register.inclusion_tag("components/badge.html")
def badge(state, label=None):
    tone = {
        "active": "ok", "paid": "ok", "confirmed": "info", "draft": "mute", "quotation": "violet",
        "paused": "warn", "closed": "mute", "cancelled": "bad", "overdue": "bad", "partial": "warn", "failed": "bad",
        "success": "ok", "open": "info",
    }.get(str(state), "mute")
    return {"label": label or str(state).replace("_", " ").title(), "tone": tone}


@register.simple_tag(takes_context=True)
def qs(context, **kw):
    """Current query string with some params replaced (for pagination/filter links)."""
    params = context["request"].GET.copy()
    for k, v in kw.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    return params.urlencode()
