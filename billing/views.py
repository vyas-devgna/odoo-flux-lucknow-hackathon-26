import csv

from django.contrib import messages
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.crud import paginate
from core.permissions import internal_required

from . import services
from .forms import PaymentForm
from .models import BillingRun, Invoice, Payment

STATUS_FILTERS = [("draft", "Draft"), ("confirmed", "Confirmed"), ("overdue", "Overdue"), ("paid", "Paid"), ("cancelled", "Cancelled")]


def filter_invoices(request, qs):
    """Shared by the invoice list, CSV export and reports so numbers always agree."""
    g = request.GET
    q, status = g.get("q", "").strip(), g.get("status", "")
    today = timezone.localdate()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(bill_to_name__icontains=q) | Q(subscription__number__icontains=q))
    if status == "overdue":
        qs = qs.filter(state=Invoice.CONFIRMED, due_date__lt=today, amount_paid__lt=F("total"))
    elif status == "outstanding":
        qs = qs.filter(state=Invoice.CONFIRMED)
    elif status:
        qs = qs.filter(state=status)
    if g.get("from"):
        qs = qs.filter(issue_date__gte=g["from"])
    if g.get("to"):
        qs = qs.filter(issue_date__lte=g["to"])
    if g.get("plan"):
        qs = qs.filter(subscription__plan_id=g["plan"])
    return qs


@internal_required
def invoice_list(request):
    qs = filter_invoices(request, Invoice.objects.select_related("subscription", "customer"))
    if request.GET.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="invoices.csv"'
        w = csv.writer(resp)
        w.writerow(["Number", "Customer", "Subscription", "Issued", "Due", "State", "Total", "Paid", "Balance"])
        for i in qs.iterator():
            w.writerow([i.number, i.bill_to_name, i.subscription.number, i.issue_date, i.due_date, i.display_state, i.total, i.amount_paid, i.balance])
        return resp
    drafts = Invoice.objects.filter(state=Invoice.DRAFT).count()
    return render(request, "billing/invoice_list.html", {
        "page": paginate(request, qs), "q": request.GET.get("q", ""), "status": request.GET.get("status", ""),
        "filters": STATUS_FILTERS, "drafts": drafts, "today": timezone.localdate(),
        "from_": request.GET.get("from", ""), "to": request.GET.get("to", "")})


@internal_required
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("subscription", "customer"), pk=pk)
    form = PaymentForm(initial={"amount": inv.balance, "date": timezone.localdate()})
    return render(request, "billing/invoice_detail.html", {
        "inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all(), "form": form})


@internal_required
@require_POST
def invoice_action(request, pk, action):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        if action == "confirm":
            services.confirm_invoice(inv)
            messages.success(request, f"{inv.number} confirmed.")
        elif action == "cancel":
            services.cancel_invoice(inv)
            messages.success(request, f"{inv.number} cancelled.")
        elif action == "send":
            services.send_invoice(inv, request)
            messages.success(request, f"{inv.number} emailed to {inv.bill_to_email}.")
        elif action == "replace":
            new = services.replace_invoice(inv)
            messages.success(request, f"Replacement {new.number} created.")
            return redirect("billing:detail", pk=new.pk)
        elif action == "pay":
            form = PaymentForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                services.record_payment(inv, amount=d["amount"], method=d["method"], date=d["date"], note=d["note"], user=request.user)
                messages.success(request, "Payment recorded.")
            else:
                messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    except services.BillingError as e:
        messages.error(request, str(e))
    except Exception as e:  # SMTP and similar delivery failures
        messages.error(request, f"Could not complete the action: {e}")
    return redirect("billing:detail", pk=pk)


@internal_required
@require_POST
def bulk_confirm(request):
    n = 0
    for inv in Invoice.objects.filter(state=Invoice.DRAFT):
        services.confirm_invoice(inv)
        n += 1
    messages.success(request, f"{n} draft invoices confirmed.")
    return redirect("billing:list")


@internal_required
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("subscription"), pk=pk)
    return render(request, "billing/print.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all()})


@internal_required
def payment_list(request):
    qs = Payment.objects.select_related("invoice", "invoice__customer", "recorded_by")
    g = request.GET
    if g.get("method"):
        qs = qs.filter(method=g["method"])
    if g.get("from"):
        qs = qs.filter(date__gte=g["from"])
    if g.get("to"):
        qs = qs.filter(date__lte=g["to"])
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    return render(request, "billing/payment_list.html", {
        "page": paginate(request, qs), "methods": Payment.METHODS, "method": g.get("method", ""), "total": total,
        "from_": g.get("from", ""), "to": g.get("to", "")})


@internal_required
def billing_runs(request):
    if request.method == "POST":
        r = services.run_billing(trigger=f"manual:{request.user.email}")
        messages.success(request, f"Billing run complete: {r.created} invoice(s) created, {r.skipped} already billed, {r.failed} failed.")
        return redirect("billing:runs")
    from subscriptions.models import Subscription
    due = Subscription.objects.filter(state="active", next_invoice_date__lte=timezone.localdate()).count()
    return render(request, "billing/runs.html", {"runs": BillingRun.objects.all()[:30], "due": due})
