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
        if callable(obj) and not hasattr(obj, "all"):
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


@register.filter
def cell(obj, spec):
    """Render a list column: 'attr.path' or 'attr.path|formatter'."""
    from django.utils.html import format_html
    path, _, fmt = spec.partition("|")
    v = getattr_path(obj, path)
    if fmt == "money":
        return money(v)
    if fmt == "state":
        return format_html('<span class="pill {}">{}</span>', "ok" if v else "mute", "Active" if v else "Inactive")
    if fmt == "yes":
        return "Yes" if v else "—"
    if fmt == "days":
        return f"{v} days"
    if path == "status":
        tone = {"live": "ok", "scheduled": "info", "expired": "mute", "exhausted": "warn", "inactive": "mute"}.get(v, "mute")
        return format_html('<span class="pill {}">{}</span>', tone, str(v).title())
    return "—" if v in (None, "") else v


_ICONS = {
    "home": '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.200-5.500 6.500-5.500s5.900 1.900 6.500 5.500M16 4.500a3.500 3.500 0 010 7M18 14.800c2 .7 3.200 2.500 3.500 5.200"/>',
    "box": '<path d="M21 8l-9-5-9 5v8l9 5 9-5zM3 8l9 5 9-5M12 13v8"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.500l9 5 9-5" transform="translate(0 -1.500)"/>',
    "repeat": '<path d="M17 2l4 4-4 4M3 11V9a3 3 0 013-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 01-3 3H3"/>',
    "file": '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8zM14 3v5h5M9 13h6M9 17h6"/>',
    "card": '<rect x="2.500" y="5" width="19" height="14" rx="3"/><path d="M2.500 10h19M6.500 15h3"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8zM7.500 7.500h.01"/>',
    "percent": '<path d="M19 5L5 19M7 9a2 2 0 100-4 2 2 0 000 4zM17 19a2 2 0 100-4 2 2 0 000 4z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.500 8-8 9-4.500-1-8-4-8-9V6z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "out": '<path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3M10 17l-5-5 5-5M5 12h11"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "moon": '<path d="M20 14.500A8 8 0 019.500 4 8 8 0 1020 14.500z"/>',
    "check": '<path d="M5 12.500l4.500 4.500L19 7.500"/>',
    "alert": '<path d="M12 3l10 18H2zM12 10v5M12 18h.01"/>',
    "print": '<path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 8l9 6 9-6"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.500-3.500"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.900 4.900l1.400 1.400M17.700 17.700l1.400 1.400M2 12h2M20 12h2M4.900 19.100l1.400-1.400M17.700 6.300l1.400-1.400"/>',
    "bell": '<path d="M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8M10.300 20a2 2 0 003.400 0"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "right": '<path d="M9 6l6 6-6 6"/>',
    "download": '<path d="M12 3v12M7 11l5 5 5-5M4 21h16"/>',
}


@register.simple_tag
def icon(name):
    from django.utils.html import mark_safe
    return mark_safe(f'<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>')


@register.simple_tag
def notifications(user):
    """Live counts for the bell. Cheap indexed counts, staff only."""
    from datetime import timedelta

    from django.db.models import F
    from django.utils import timezone

    from billing.models import Invoice
    from subscriptions.models import Subscription
    if not getattr(user, "is_internal", False):
        return {"items": [], "total": 0}
    today = timezone.localdate()
    spec = [
        (Invoice.objects.filter(state="confirmed", due_date__lt=today, amount_paid__lt=F("total")).count(), "overdue invoices to chase", "/billing/invoices/?status=overdue", "bad"),
        (Invoice.objects.filter(state="draft").count(), "draft invoices to review", "/billing/invoices/?status=draft", "go"),
        (Subscription.objects.filter(state="active", next_invoice_date__lte=today).count(), "subscriptions due for billing", "/billing/runs/", "go"),
        (Subscription.objects.filter(state="confirmed").count(), "accepted subscriptions waiting for activation", "/subscriptions/?state=confirmed", "go"),
        (Subscription.objects.filter(state="quotation", quote_valid_until__lte=today + timedelta(days=3)).count(), "quotations expiring in 3 days", "/subscriptions/?state=quotation", ""),
        (Subscription.objects.filter(state="active", expiry_date__gte=today, expiry_date__lte=today + timedelta(days=14)).count(), "subscriptions expiring in 14 days", "/subscriptions/?state=active", ""),
    ]
    items = [{"n": n, "text": t, "url": u, "tone": tone} for n, t, u, tone in spec if n]
    return {"items": items, "total": sum(i["n"] for i in items if i["tone"] in ("bad", "go"))}


@register.filter
def avatar_color(name):
    palette = ["#6d4cf0", "#2559e0", "#0b7a45", "#b4500a", "#c8341a", "#0a7580", "#a21caf", "#475569"]
    return palette[sum(map(ord, str(name))) % len(palette)]


@register.simple_tag(takes_context=True)
def sorth(context, key, label, right=False):
    from django.utils.html import format_html
    cur = context.get("sort", "")
    nxt = f"-{key}" if cur == key else key
    arrow = " ↑" if cur == key else " ↓" if cur == f"-{key}" else ""
    params = context["request"].GET.copy()
    params["sort"] = nxt
    params.pop("page", None)
    return format_html('<th class="{}"><a href="?{}">{}{}</a></th>', "right" if right else "", params.urlencode(), label, arrow)


@register.simple_tag(takes_context=True)
def flash_json(context):
    """Django messages as a JSON array for the toast layer. Escaped so it is safe inside a <script> block."""
    import json
    from django.contrib.messages import get_messages
    from django.utils.safestring import mark_safe
    msgs = [{"t": m.tags, "m": str(m)} for m in get_messages(context["request"])]
    return mark_safe(json.dumps(msgs).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")) if msgs else ""


@register.filter
def make_list_words(value):
    return str(value).split()


@register.filter
def dictget(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.simple_tag
def idem_key():
    import uuid
    return uuid.uuid4().hex


@register.filter
def pct(value, total):
    try:
        return f"{(Decimal(value) / Decimal(total) * 100):.1f}"
    except Exception:
        return "0.0"
