"""Navigation model: one place decides the top-level section and contextual sub-navigation for every page."""
from django.urls import reverse

TOP = [  # key, label, url name, required caps (any); admins pass everything
    ("overview", "Overview", "core:dashboard", ()),
    ("sales", "Sales", "subscriptions:quotes", ("sales",)),
    ("subscriptions", "Subscriptions", "subscriptions:list", ("sales", "billing")),
    ("billing", "Billing", "billing:list", ("billing", "finance")),
    ("customers", "Customers", "core:customers", ("sales", "billing", "finance")),
    ("catalog", "Catalog", "catalog:products_list", ()),
    ("reports", "Reports", "core:reports", ("finance",)),
]

COMMANDS = [
    ("New subscription", "subscriptions:new", "Create", ("sales",), ""),
    ("New customer", "core:customer_new", "Create", ("sales",), ""),
    ("Run billing", "billing:runs", "Action", ("billing",), ""),
    ("Overdue invoices", "billing:list", "View", ("billing", "finance"), "?status=overdue"),
    ("Draft invoices", "billing:list", "View", ("billing", "finance"), "?status=draft"),
    ("Payments awaiting confirmation", "billing:payments", "View", ("billing", "finance"), "?status=pending"),
]

SUB = {
    "sales": [("Quotations", "subscriptions:quotes"), ("Pipeline", "subscriptions:pipeline"), ("Templates", "catalog:templates_list"), ("Storefront", "store:home")],
    "subscriptions": [("All subscriptions", "subscriptions:list"), ("Renewals", "subscriptions:renewals"), ("Changes", "subscriptions:changes")],
    "billing": [("Invoices", "billing:list"), ("Payments", "billing:payments"), ("Credit notes", "billing:credit_notes"), ("Billing runs", "billing:runs")],
    "catalog": [("Products", "catalog:products_list"), ("Plans", "catalog:plans_list"), ("Discounts", "catalog:discounts_list"), ("Taxes", "catalog:taxes_list"), ("Categories", "catalog:categories_list")],
    "reports": [("Revenue", "core:reports"), ("Recurring revenue", "core:report_mrr"), ("Retention", "core:report_retention"), ("Receivables", "core:report_receivables"), ("Breakdown", "core:report_breakdown")],
    "settings": [("Company", "core:settings"), ("Team", "accounts:team"), ("Payments", "core:settings_payments"), ("Automation", "core:automation"), ("Email outbox", "core:outbox"), ("Audit log", "core:audit")],
}


def section_of(match):
    if match is None:
        return "overview"
    ns, name = match.namespace, match.url_name or ""
    if ns == "subscriptions":
        return "sales" if name in ("quotes", "pipeline") or name == "new" else "subscriptions"
    if ns == "billing":
        return "billing"
    if ns == "catalog":
        return "sales" if name.startswith("templates") else "catalog"
    if ns == "accounts":
        return "settings" if name.startswith("team") else "overview"
    if ns == "store":
        return "sales"
    if name in ("customers", "customer_new", "customer_edit", "customer_import", "customer_statement"):
        return "customers"
    if name.startswith("report"):
        return "reports"
    if name in ("settings", "settings_payments", "automation", "automation_rule", "template_edit", "outbox", "audit"):
        return "settings"
    return "overview"


def shell(request):
    """Template context for the staff shell. Cheap: URL reversing only."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_internal:
        return {}
    match = request.resolver_match
    section = section_of(match)
    full = f"{match.namespace}:{match.url_name}" if match and match.namespace else (match.url_name if match else "")
    top = []
    for key, label, url, caps in TOP:
        if caps and not any(user.has_cap(c) for c in caps):
            continue
        top.append({"key": key, "label": label, "url": reverse(url), "on": key == section})
    subnav = []
    for label, url in SUB.get(section, []):
        if section == "settings" and not user.is_admin_role and url != "core:settings":
            continue
        try:
            href = reverse(url)
        except Exception:
            continue
        on = full == url or (url.endswith("_list") and full.startswith(url[:-5])) or (url == "subscriptions:list" and full in ("subscriptions:detail", "subscriptions:edit", "subscriptions:change"))
        subnav.append({"label": label, "url": href, "on": on})
    if section in ("sales",) and match and match.url_name in ("detail",):
        subnav = []
    commands = [{"t": label, "u": reverse(url) + query, "k": kind} for label, url, kind, caps, query in COMMANDS
                if any(user.has_cap(cap) for cap in caps)]
    return {"NAV_TOP": top, "SUBNAV": subnav, "SECTION": section, "COMMANDS": commands}
