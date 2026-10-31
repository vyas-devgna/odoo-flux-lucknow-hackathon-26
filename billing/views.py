import csv

from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import audit, csvsafe, reports
from core.crud import apply_sort, paginate
from core.permissions import cap_required, safe_next

from . import providers, services
from .forms import CreditNoteForm, PaymentForm, RefundForm
from .models import BillingFailure, BillingRun, Invoice, Payment

billing_required = cap_required("billing", "finance")
finance_required = cap_required("finance")
billing_only = cap_required("billing")

STATUS_FILTERS = [("draft", "Draft"), ("confirmed", "Confirmed"), ("overdue", "Overdue"), ("paid", "Paid"), ("cancelled", "Cancelled")]


def filter_invoices(request, qs):
    """Shared by the invoice list and CSV export so numbers always agree."""
    g = request.GET
    q, status = g.get("q", "").strip(), g.get("status", "")
    today = timezone.localdate()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(bill_to_name__icontains=q) | Q(subscription__number__icontains=q))
    if status == "overdue":
        qs = qs.filter(state=Invoice.CONFIRMED, due_date__lt=today).exclude(kind="credit_note").filter(total__gt=F("amount_paid") + F("credited_total"))
    elif status == "outstanding":
        qs = qs.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note")
    elif status:
        qs = qs.filter(state=status)
    if g.get("from"):
        qs = qs.filter(issue_date__gte=g["from"])
    if g.get("to"):
        qs = qs.filter(issue_date__lte=g["to"])
    if g.get("plan"):
        qs = qs.filter(subscription__plan_id=g["plan"])
    return qs


@billing_required
def invoice_list(request):
    qs = filter_invoices(request, Invoice.objects.select_related("subscription", "customer").exclude(kind="credit_note"))
    if request.GET.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="invoices.csv"'
        w = csv.writer(resp)
        w.writerow(["Number", "Customer", "Subscription", "Kind", "Issued", "Due", "State", "Total", "Credited", "Paid", "Balance"])
        for i in qs.iterator():
            w.writerow(csvsafe.row([i.number, i.bill_to_name, i.subscription.number, i.kind, i.issue_date, i.due_date, i.display_state, i.total, i.credited_total, i.amount_paid, i.balance]))
        return resp
    qs, sort = apply_sort(request, qs, {"issued": "issue_date", "due": "due_date", "amount": "total"}, "-issued")
    page = paginate(request, qs)
    groups = reports.group_by_day(list(page.object_list), key="issue_date") if sort.lstrip("-") == "issued" else [{"label": "", "items": list(page.object_list), "total": None}]
    counts = {k: n for k, n in Invoice.objects.exclude(kind="credit_note").values_list("state").annotate(n=Count("id"))}
    return render(request, "billing/invoice_list.html", {
        "groups": groups, "sort": sort, "counts": counts, "page": page, "q": request.GET.get("q", ""), "status": request.GET.get("status", ""),
        "filters": STATUS_FILTERS, "drafts": counts.get("draft", 0), "today": timezone.localdate(), "from_": request.GET.get("from", ""), "to": request.GET.get("to", ""), "title": "Invoices"})


@billing_required
def credit_notes(request):
    qs = Invoice.objects.filter(kind="credit_note").select_related("credit_for", "customer")
    qs, sort = apply_sort(request, qs, {"issued": "issue_date", "amount": "total"}, "-issued")
    return render(request, "billing/credit_notes.html", {"page": paginate(request, qs), "sort": sort, "title": "Credit notes"})


@billing_required
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("subscription__plan", "customer", "credit_for", "replaces"), pk=pk)
    ctx = {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.select_related("recorded_by"), "title": inv.number,
           "pay_form": PaymentForm(initial={"amount": inv.balance, "date": timezone.localdate(), "method": "bank"}),
           "refund_form": RefundForm(initial={"date": timezone.localdate(), "amount": inv.amount_paid, "method": "bank"}),
           "credit_form": CreditNoteForm(initial={"amount": inv.total - inv.credited_total}), "links": inv.payment_links.all()[:3],
           "pending": inv.payments.filter(status=Payment.PENDING), "credit_notes": inv.credit_notes.all(), "provider_on": providers.configured(),
           "can_pay": request.user.has_cap("finance"), "can_bill": request.user.has_cap("billing")}
    return render(request, "billing/invoice_detail.html", ctx)


@billing_required
def invoice_pdf(request, pk):
    from .pdf import render_invoice_pdf
    inv = get_object_or_404(Invoice.objects.select_related("subscription", "customer"), pk=pk)
    resp = HttpResponse(render_invoice_pdf(inv), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number.replace("/", "-")}.pdf"'
    return resp


def _need(request, cap):
    if not request.user.has_cap(cap):
        raise Http404  # hidden from staff without the capability; PermissionDenied would leak that the action exists


@billing_required
@require_POST
def invoice_action(request, pk, action):
    inv = get_object_or_404(Invoice, pk=pk)
    money_actions = {"pay", "refund", "confirm_payment", "reject_payment", "link", "reconcile"}
    _need(request, "finance" if action in money_actions else "billing")
    try:
        if action == "confirm":
            services.confirm_invoice(inv)
            audit.log(request.user, "invoice.confirm", inv)
            messages.success(request, f"{inv.number} confirmed and locked.")
        elif action == "cancel":
            services.cancel_invoice(inv)
            audit.log(request.user, "invoice.cancel", inv)
            messages.success(request, f"{inv.number} cancelled.")
        elif action == "send":
            services.send_invoice(inv, request)
            audit.log(request.user, "invoice.send", inv, inv.bill_to_email)
            messages.success(request, f"{inv.number} queued for {inv.bill_to_email}.")
        elif action == "remind":
            services.send_reminder(inv, request.user)
            messages.success(request, f"Reminder queued for {inv.bill_to_email}.")
        elif action == "replace":
            new = services.replace_invoice(inv)
            audit.log(request.user, "invoice.replace", inv, f"replaced by {new.number}")
            messages.success(request, f"Replacement {new.number} created.")
            return redirect("billing:detail", pk=new.pk)
        elif action == "credit":
            form = CreditNoteForm(request.POST)
            if form.is_valid():
                cn = services.issue_credit_note(inv, amount=form.cleaned_data["amount"], reason=form.cleaned_data["reason"], actor=request.user)
                messages.success(request, f"Credit note {cn.number} issued.")
            else:
                messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
        elif action == "pay":
            form = PaymentForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                services.record_payment(inv, amount=d["amount"], method=d["method"], date=d["date"], note=d["note"], reference=d["reference"], user=request.user, idempotency_key=d["idem"] or None)
                messages.success(request, "Payment recorded.")
            else:
                messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
        elif action == "refund":
            form = RefundForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                services.refund_payment(inv, amount=d["amount"], method=d["method"], date=d["date"], note=d["note"], user=request.user)
                messages.success(request, f"Refund of {d['amount']} recorded.")
            else:
                messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
        elif action in ("confirm_payment", "reject_payment"):
            pay = get_object_or_404(inv.payments, pk=request.POST.get("payment"))
            if action == "confirm_payment":
                services.confirm_pending(pay, user=request.user)
                messages.success(request, "Payment confirmed against the invoice.")
            else:
                services.reject_pending(pay, reason=request.POST.get("reason", ""), user=request.user)
                messages.success(request, "Payment report rejected.")
        elif action == "link":
            link = providers.create_payment_link(inv)
            audit.log(request.user, "payment.link", inv, link.provider_id)
            messages.success(request, "Payment link created. Share it with the customer.")
        elif action == "reconcile":
            link = get_object_or_404(inv.payment_links, pk=request.POST.get("link"))
            messages.success(request, f"Provider says: {providers.reconcile_link(link)}.")
        else:
            raise Http404
    except services.BillingError as e:
        messages.error(request, str(e))
    return redirect("billing:detail", pk=pk)


@billing_only
@require_POST
def bulk_confirm(request):
    n = 0
    for inv in Invoice.objects.filter(state=Invoice.DRAFT).exclude(kind="credit_note"):
        services.confirm_invoice(inv)
        n += 1
    audit.log(request.user, "invoice.confirm_bulk", detail=f"{n} drafts")
    messages.success(request, f"{n} draft invoices confirmed.")
    return redirect("billing:list")


@billing_only
@require_POST
def bulk_selected(request):
    ids = request.POST.getlist("ids")
    action = request.POST.get("action")
    ok = bad = 0
    for inv in Invoice.objects.filter(pk__in=ids):
        try:
            if action == "confirm":
                services.confirm_invoice(inv)
            elif action == "send":
                services.send_invoice(inv, request)
            elif action == "remind":
                services.send_reminder(inv, request.user)
            elif action == "cancel":
                services.cancel_invoice(inv)
            else:
                raise services.BillingError("Unknown action.")
            audit.log(request.user, f"invoice.{action}", inv, "bulk")
            ok += 1
        except Exception:
            bad += 1
    if ok:
        messages.success(request, f"{ok} invoice(s) updated ({action}).")
    if bad:
        messages.warning(request, f"{bad} skipped (wrong state or not eligible).")
    return redirect(safe_next(request, "billing:list"))


@billing_only
@require_POST
def bulk_remind(request):
    from core import automation, mail
    res = automation.run_scheduled()
    mail.flush()
    messages.success(request, f"Automation ran: {sum(res.values())} message(s) queued across {len(res)} rule types.")
    return redirect("billing:list")


@billing_required
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("subscription"), pk=pk)
    return render(request, "billing/print.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.filter(status="succeeded")})


@billing_required
def payment_list(request):
    qs = Payment.objects.select_related("invoice", "invoice__customer", "recorded_by")
    g = request.GET
    status = g.get("status", "")
    if g.get("method"):
        qs = qs.filter(method=g["method"])
    if status:
        qs = qs.filter(status=status)
    if g.get("from"):
        qs = qs.filter(date__gte=g["from"])
    if g.get("to"):
        qs = qs.filter(date__lte=g["to"])
    counts = dict(Payment.objects.values_list("status").annotate(n=Count("id")))
    total = qs.filter(status="succeeded", is_test=False).aggregate(t=Sum("amount"))["t"] or 0
    page = paginate(request, qs)
    groups = reports.group_by_day(list(page.object_list), key="date")
    open_invoices = Invoice.objects.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note").order_by("due_date")[:60]
    return render(request, "billing/payment_list.html", {
        "page": page, "groups": groups, "open_invoices": open_invoices, "today": timezone.localdate(), "pay_methods": [(k, l) for k, l in Payment.METHODS if k != "razorpay"],
        "methods": Payment.METHODS, "method": g.get("method", ""), "status": status, "counts": counts, "total": total, "from_": g.get("from", ""), "to": g.get("to", ""),
        "pay_form": PaymentForm(initial={"date": timezone.localdate()}), "can_pay": request.user.has_cap("finance"), "title": "Payments"})


@billing_required
def billing_runs(request):
    if request.method == "POST":
        _need(request, "billing")
        if request.POST.get("retry"):
            r = services.retry_failures(request.user)
            messages.success(request, "Retried failed subscriptions." if r else "Nothing to retry.")
        else:
            r = services.run_billing(trigger=f"manual:{request.user.email}")
            audit.log(request.user, "billing.run", detail=f"created {r.created}, skipped {r.skipped}, failed {r.failed}")
            messages.success(request, f"Billing run complete: {r.created} invoice(s) created, {r.skipped} already billed, {r.failed} failed.")
        return redirect("billing:runs")
    from subscriptions.models import Subscription
    today = timezone.localdate()
    due = Subscription.objects.filter(state="active", next_invoice_date__lte=today).count()
    return render(request, "billing/runs.html", {
        "runs": BillingRun.objects.all()[:30], "due": due, "failures": BillingFailure.objects.filter(resolved_at__isnull=True).select_related("subscription__customer", "run")[:50],
        "upcoming": Subscription.objects.filter(state="active", next_invoice_date__gt=today, next_invoice_date__lte=today + timezone.timedelta(days=7)).count(),
        "pending": Payment.objects.filter(status="pending").count(), "title": "Billing runs"})


@finance_required
@require_POST
def quick_pay(request):
    inv = get_object_or_404(Invoice, pk=request.POST.get("invoice") or 0)
    form = PaymentForm(request.POST)
    nxt = safe_next(request, "core:dashboard")
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.record_payment(inv, amount=d["amount"], method=d["method"], date=d["date"], note=d["note"], reference=d["reference"], user=request.user, idempotency_key=d["idem"] or None)
            messages.success(request, f"{d['amount']} recorded on {inv.number}.")
        except services.BillingError as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    return redirect(nxt)


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Signature-verified, deduplicated, idempotent. Always 200 for verified events so the provider stops retrying."""
    import json
    body = request.body
    if not providers.verify_signature(body, request.headers.get("X-Razorpay-Signature", "")):
        return HttpResponse(status=400)
    try:
        payload = json.loads(body.decode())
    except ValueError:
        return HttpResponse(status=400)
    event_id = request.headers.get("X-Razorpay-Event-Id") or payload.get("id") or ""
    if not event_id:
        return HttpResponse(status=400)
    outcome = providers.handle_event(event_id, payload)
    return JsonResponse({"outcome": outcome})
