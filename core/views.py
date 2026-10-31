import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError as VE
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Customer
from billing.models import Invoice, Payment
from catalog.models import Plan, Product, Tax
from subscriptions.models import Subscription

from . import audit, reports
from .crud import paginate
from .models import Notification, Task
from .permissions import cap_required, internal_required

sales_required = cap_required("sales")
any_ops = cap_required("sales", "billing", "finance")


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
    rate = min(int(s["cash"] / s["invoiced"] * 100), 100) if s["invoiced"] > 0 else 0
    overdue_pct = min(int(s["overdue"] / s["outstanding"] * 100), 100) if s["outstanding"] else 0
    series = reports.mrr_series(12)
    cash = reports.month_series(12)
    feed = reports.group_by_day(reports.activity_feed(14))
    open_invoices = Invoice.objects.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note").order_by("due_date")[:60] if request.user.has_cap("finance") else []
    renewals = (Subscription.objects.filter(state=Subscription.ACTIVE, expiry_date__lte=today + timedelta(days=45), expiry_date__gte=today)
                .select_related("customer", "plan").order_by("expiry_date")[:5])
    empty = not Subscription.objects.exists()
    setup = [("Add a tax rule", "catalog:taxes_list", Tax.objects.exists()), ("Create a product", "catalog:products_list", Product.objects.exists()),
             ("Create a plan", "catalog:plans_list", Plan.objects.exists()), ("Add your first customer", "core:customer_new", Customer.objects.exists()),
             ("Quote a subscription", "subscriptions:new", Subscription.objects.exists())] if empty else []
    cur, prev = series[-1], series[-2]
    my_tasks = Task.objects.filter(done_at__isnull=True).filter(Q(assignee=request.user) | Q(assignee__isnull=True))[:5]
    return render(request, "core/dashboard.html", {
        "s": s, "rng": rng, "ranges": reports.RANGES, "rate": rate, "overdue_pct": overdue_pct, "title": "Overview",
        "mrr_delta": cur["mrr"] - prev["mrr"],
        "mrr_cfg": {"type": "line", "labels": [r["label"] for r in series], "datasets": [{"label": "MRR", "data": [float(r["closing"]) for r in series], "color": "accent", "fill": True}]},
        "feed": feed, "open_invoices": open_invoices, "cal": reports.billing_calendar(today), "renewals": renewals, "today": today, "tasks": my_tasks,
        "drafts": Invoice.objects.filter(state=Invoice.DRAFT).count(), "setup": setup, "empty": empty,
        "due_now": Subscription.objects.filter(state="active", next_invoice_date__lte=today).count(),
        "quotes_open": Subscription.objects.filter(state="quotation").count(),
        "failures": Subscription.objects.filter(payment_exception=True, state__in=["active", "paused"]).count(),
        "pending_pay": Payment.objects.filter(status="pending").count(),
        "methods": [(k, l) for k, l in Payment.METHODS if k != "razorpay"], "can_finance": request.user.has_cap("finance")})


@internal_required
@require_POST
def task_done(request, pk):
    Task.objects.filter(pk=pk, done_at__isnull=True).update(done_at=timezone.now())
    return redirect(request.POST.get("next") or "core:dashboard")


# ---- customers ----
@any_ops
def customer_list(request):
    q = request.GET.get("q", "").strip()
    qs = Customer.objects.annotate(n_subs=Count("subscriptions", distinct=True)).order_by("name", "id")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q))
    return render(request, "core/customers.html", {"page": paginate(request, qs), "q": q, "title": "Customers"})


@sales_required
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
    ctx = {"form": form, "obj": obj, "title": str(obj) if obj else "New customer"}
    if obj:
        ctx["subs"] = obj.subscriptions.select_related("plan")[:20]
        ctx["invoices"] = obj.invoices.exclude(kind="credit_note")[:10]
        ctx["owed"] = obj.invoices.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note").aggregate(t=Sum(reports.BALANCE))["t"] or Decimal(0)
        ctx["paid"] = obj.invoices.aggregate(t=Sum("amount_paid"))["t"] or Decimal(0)
        ctx["mrr"] = sum((s.recurring_monthly for s in obj.subscriptions.filter(state="active")), Decimal(0))
        ctx["timeline"] = audit.timeline(obj)
    return render(request, "core/customer_form.html", ctx)


@sales_required
def customer_import(request):
    """Bring customers over from a spreadsheet. Rows are matched by email: new ones are created, existing ones updated."""
    report = None
    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        if f.size > 2_000_000:
            messages.error(request, "File too large (2 MB max).")
            return redirect("core:customer_import")
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            messages.error(request, "Please upload a UTF-8 CSV file.")
            return redirect("core:customer_import")
        rdr = csv.DictReader(io.StringIO(text))
        cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}
        if "email" not in cols or "name" not in cols:
            messages.error(request, "The first row must contain at least the headers: name, email.")
            return redirect("core:customer_import")
        created = updated = 0
        errors = []
        with transaction.atomic():
            for n, row in enumerate(rdr, start=2):
                g = lambda k: (row.get(cols.get(k, ""), "") or "").strip()
                email = g("email").lower()
                try:
                    validate_email(email)
                    if not g("name"):
                        raise VE("name is required")
                except VE as e:
                    errors.append(f"Row {n}: {email or '(no email)'} – {e.messages[0]}")
                    continue
                obj = Customer.objects.filter(email__iexact=email).first()
                vals = {"name": g("name"), "company": g("company"), "phone": g("phone"), "address": g("address"), "tax_id": g("tax_id")}
                if obj:
                    for k, v in vals.items():
                        if v:
                            setattr(obj, k, v)
                    obj.save()
                    updated += 1
                else:
                    Customer.objects.create(email=email, **vals)
                    created += 1
        audit.log(request.user, "customer.import", detail=f"{created} created, {updated} updated, {len(errors)} rejected")
        report = {"created": created, "updated": updated, "errors": errors[:50], "n_errors": len(errors)}
    return render(request, "core/customer_import.html", {"report": report, "title": "Import customers"})


@any_ops
def customer_statement(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    invs = list(c.invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED]).order_by("issue_date", "id").prefetch_related("payments"))
    rows = []
    for i in invs:
        rows.append({"date": i.issue_date, "ref": i.number, "desc": ("Credit note" if i.is_credit_note else f"Invoice {i.period_start:%d %b}–{i.period_end:%d %b %Y}"),
                     "debit": i.total if i.total > 0 else None, "credit": -i.total if i.total < 0 else None})
        for p in i.payments.all():
            if p.status != "succeeded":
                continue
            rows.append({"date": p.date, "ref": i.number, "desc": f"{'Refund' if p.amount < 0 else 'Payment'} · {p.get_method_display()}",
                         "debit": -p.amount if p.amount < 0 else None, "credit": p.amount if p.amount > 0 else None})
    rows.sort(key=lambda r: r["date"])
    run = Decimal(0)
    for r in rows:
        run += (r["debit"] or 0) - (r["credit"] or 0)
        r["bal"] = run
    return render(request, "core/statement.html", {"c": c, "rows": rows, "balance": run, "today": timezone.localdate(), "title": f"Statement · {c}"})


# ---- search / export / health ----
@internal_required
def search(request):
    q = request.GET.get("q", "").strip()
    res = {}
    if len(q) >= 2:
        res["customers"] = list(Customer.objects.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(email__icontains=q))[:8])
        res["subscriptions"] = list(Subscription.objects.filter(Q(number__icontains=q) | Q(customer__name__icontains=q) | Q(customer__company__icontains=q)).select_related("customer", "plan")[:8])
        res["invoices"] = list(Invoice.objects.filter(Q(number__icontains=q) | Q(bill_to_name__icontains=q)).select_related("subscription")[:8])
    if request.GET.get("fmt") == "json":
        out = [{"t": c.display_name, "u": reverse("core:customer_edit", args=[c.pk]), "k": "Customers", "sub": c.email} for c in res.get("customers", [])]
        out += [{"t": s.number, "u": reverse("subscriptions:detail", args=[s.pk]), "k": "Subscriptions", "sub": f"{s.customer} · {s.get_state_display()}"} for s in res.get("subscriptions", [])]
        out += [{"t": i.number, "u": reverse("billing:detail", args=[i.pk]), "k": "Invoices", "sub": f"{i.bill_to_name} · {i.display_state}"} for i in res.get("invoices", [])]
        return JsonResponse({"results": out})
    return render(request, "core/search.html", {"q": q, "res": res, "any": any(res.values()), "title": "Search"})


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
    u = request.user
    if what == "subscriptions" and any(u.has_cap(c) for c in ("sales", "billing", "finance")):
        qs = Subscription.objects.select_related("customer", "plan").prefetch_related("lines__tax")
        if request.GET.get("state"):
            qs = qs.filter(state=request.GET["state"])
        return _csv("subscriptions", ["Number", "Customer", "Email", "Plan", "State", "Start", "Expiry", "Next invoice", "Monthly value"],
                    ([s.number, s.customer, s.customer.email, s.plan.name, s.state, s.start_date, s.expiry_date, s.next_invoice_date, s.recurring_monthly] for s in qs.iterator(chunk_size=500)))
    if what == "payments" and u.has_cap("finance"):
        qs = Payment.objects.select_related("invoice")
        return _csv("payments", ["Date", "Invoice", "Customer", "Method", "Status", "Amount", "Reference", "Test"],
                    ([p.date, p.invoice.number, p.invoice.bill_to_name, p.method, p.status, p.amount, p.reference or p.note, p.is_test] for p in qs.iterator(chunk_size=500)))
    if what == "customers" and any(u.has_cap(c) for c in ("sales", "billing", "finance")):
        return _csv("customers", ["Name", "Company", "Email", "Phone", "Tax ID"], ([c.name, c.company, c.email, c.phone, c.tax_id] for c in Customer.objects.order_by("name").iterator(chunk_size=500)))
    return HttpResponse(status=404)


def healthz(request):
    try:
        with connection.cursor() as c:
            c.execute("select 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "db_error"}, status=503)
