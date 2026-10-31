from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import providers, services as billing
from billing.forms import ReportPaymentForm
from billing.models import Invoice, Payment
from catalog.models import Product
from subscriptions import services
from subscriptions.forms import CloseForm
from subscriptions.models import Subscription

from . import audit, reports
from .crud import paginate
from .models import CompanySettings
from .permissions import customer_required


def _mine(request):
    return request.user.customer


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


def _subs(request):
    return _mine(request).subscriptions.exclude(state__in=[Subscription.DRAFT])


def _invoices(request):
    return _mine(request).invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED])


@customer_required
def home(request):
    c = _mine(request)
    subs = _subs(request).select_related("plan").prefetch_related("lines__tax", "lines__product")
    invs = _invoices(request).exclude(kind="credit_note")
    owed = invs.filter(state=Invoice.CONFIRMED).aggregate(t=Sum(reports.BALANCE))["t"] or Decimal(0)
    live = [s for s in subs if s.state in ("active", "paused")]
    quotes = [s for s in subs if s.state in ("quotation", "confirmed")]
    today = timezone.localdate()
    overdue = invs.filter(state=Invoice.CONFIRMED, due_date__lt=today)
    nxt = min((s for s in live if s.next_invoice_date), key=lambda s: s.next_invoice_date, default=None)
    return render(request, "portal/home.html", {
        "subs": live[:6], "quotes": quotes, "invoices": invs[:6], "owed": owed, "active": sum(1 for s in live if s.state == "active"), "open_count": invs.filter(state=Invoice.CONFIRMED).count(),
        "overdue": list(overdue), "next_sub": nxt, "pending": Payment.objects.filter(invoice__customer=c, status="pending").count(), "title": f"Hello, {request.user.name or 'there'}"})


@customer_required
def subscriptions(request):
    return render(request, "portal/subscriptions.html", {"page": paginate(request, _subs(request).select_related("plan")), "title": "Subscriptions"})


@customer_required
def subscription(request, pk):
    sub = get_object_or_404(_subs(request).select_related("plan"), pk=pk)
    lines = list(sub.lines.select_related("product", "variant", "tax"))
    return render(request, "portal/subscription.html", {
        "sub": sub, "lines": lines, "stages": Subscription.FLOW, "title": sub.number, "tot": {"subtotal": sum((l.net_amount for l in lines), 0), "tax": sum((l.tax_amount for l in lines), 0)},
        "invoices": sub.invoices.exclude(state__in=[Invoice.DRAFT, Invoice.CANCELLED]).exclude(kind="credit_note")[:12],
        "addons": sub.plan.products.filter(type=Product.ADDON, active=True), "enabled_addons": {l.product_id for l in lines if l.product_id and l.is_addon},
        "close_form": CloseForm(), "scheduled": sub.scheduled_changes.filter(applied_at__isnull=True, cancelled_at__isnull=True),
        "acceptance": sub.acceptances.first(), "renewals": sub.renewals.exclude(state="cancelled")})


@customer_required
@require_POST
def action(request, pk, action):
    sub = get_object_or_404(_subs(request), pk=pk)
    actor = request.user
    try:
        if action == "accept":
            name = request.POST.get("signed_name", "").strip()
            if not name or not request.POST.get("agree"):
                raise services.TransitionError("Type your full name and tick the box to accept.")
            services.accept(sub, actor=actor, signed_name=name, ip=_client_ip(request), how="Accepted online",
                            expected_revision=request.POST.get("revision", ""))
            audit.log(actor, "portal.accept", sub, name)
            messages.success(request, "Thank you. Your quotation is accepted.")
        elif action in ("pause", "resume"):
            if action == "pause" and not sub.plan.self_serve_cancel and not sub.plan.allow_pause:
                raise services.TransitionError("This plan can't be paused online.")
            getattr(services, action)(sub, actor)
            audit.log(actor, f"portal.{action}", sub)
            messages.success(request, "Done.")
        elif action == "renew":
            new = services.renew(sub, actor=actor, accept=True)
            audit.log(actor, "portal.renew", sub, f"renewed as {new.number}")
            messages.success(request, f"Renewed. Your new term {new.number} starts {new.start_date:%d %b %Y}.")
            return redirect("core:portal_subscription", pk=new.pk)
        elif action == "close":
            form = CloseForm(request.POST)
            if not form.is_valid():
                raise services.TransitionError("Please tell us why you're leaving.")
            d = form.cleaned_data
            services.close(sub, actor=actor, reason=d["reason"], note=d["note"], by_customer=True)
            audit.log(actor, "portal.close", sub, d["reason"])
            messages.success(request, "Your cancellation is recorded." + (f" Your subscription ends on {Subscription.objects.get(pk=sub.pk).pending_cancel_at:%d %b %Y}." if Subscription.objects.get(pk=sub.pk).pending_cancel_at else ""))
        elif action == "keep":
            services.keep(sub, actor)
            messages.success(request, "Glad you're staying. Your cancellation was withdrawn.")
        elif action in ("addon_on", "addon_off"):
            if not sub.plan.self_serve_changes:
                raise services.TransitionError("Plan changes aren't available online for this plan.")
            res = services.set_addon(sub, request.POST.get("product"), action == "addon_on", actor=actor)
            audit.log(actor, f"portal.{action}", sub, f"product {request.POST.get('product')}")
            messages.success(request, "Add-on updated." + (f" It takes effect on {res['scheduled_for']:%d %b %Y}." if res and res.get("scheduled_for") else ""))
        else:
            messages.error(request, "That action isn't available.")
    except services.TransitionError as e:
        messages.error(request, str(e))
    return redirect("core:portal_subscription", pk=pk)


@customer_required
def change(request, pk):
    from subscriptions.views import change_view
    sub = get_object_or_404(_subs(request).select_related("plan"), pk=pk)
    if not sub.plan.self_serve_changes or not sub.can("change"):
        messages.error(request, "Online changes aren't available for this subscription. Please contact us.")
        return redirect("core:portal_subscription", pk=pk)
    return change_view(request, sub, portal=True)


@customer_required
def invoices(request):
    qs = _invoices(request).select_related("subscription")
    due = request.GET.get("status") == "due"
    if due:
        qs = qs.filter(state=Invoice.CONFIRMED).exclude(kind="credit_note")
    return render(request, "portal/invoices.html", {"page": paginate(request, qs), "due_only": due, "title": "Invoices", "provider_on": providers.configured()})


@customer_required
def invoice(request, pk):
    inv = get_object_or_404(_invoices(request), pk=pk)
    return render(request, "portal/invoice.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.exclude(status="failed"), "title": inv.number,
                                                   "provider_on": providers.configured(), "pending": inv.payments.filter(status="pending").first()})


@customer_required
def invoice_pdf(request, pk):
    from billing.pdf import render_invoice_pdf
    inv = get_object_or_404(_invoices(request), pk=pk)
    resp = HttpResponse(render_invoice_pdf(inv), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{inv.number.replace("/", "-")}.pdf"'
    return resp


@customer_required
def invoice_print(request, pk):
    inv = get_object_or_404(_invoices(request), pk=pk)
    from django.urls import reverse
    return render(request, "documents/invoice.html", {"inv": inv, "lines": inv.lines.all(), "payments": inv.payments.filter(status="succeeded"), "embed": bool(request.GET.get("embed")),
                                                      "pdf_url": reverse("core:portal_invoice_pdf", args=[inv.pk])})


@customer_required
def pay(request, pk):
    inv = get_object_or_404(_invoices(request).select_related("subscription"), pk=pk)
    co = CompanySettings.get()
    if inv.state != Invoice.CONFIRMED or inv.balance <= 0 or inv.is_credit_note:
        messages.info(request, "This invoice has nothing left to pay.")
        return redirect("core:portal_invoice", pk=pk)
    form = ReportPaymentForm(request.POST or None, initial={"amount": inv.balance, "date": timezone.localdate(), "method": "upi"})
    if request.method == "POST":
        if request.POST.get("online"):
            try:
                link = providers.create_payment_link(inv)
                return redirect(link.url)
            except billing.BillingError as e:
                messages.error(request, str(e))
        elif form.is_valid():
            d = form.cleaned_data
            try:
                billing.report_payment(inv, amount=d["amount"], method=d["method"], reference=d["reference"], date=d["date"], user=request.user)
                messages.success(request, "Thanks. We'll confirm it as soon as it shows on our statement.")
                return redirect("core:portal_invoice", pk=pk)
            except billing.BillingError as e:
                messages.error(request, str(e))
    return render(request, "portal/pay.html", {"inv": inv, "form": form, "co": co, "provider_on": providers.configured(), "test_mode": providers.is_test_mode(),
                                               "pending": inv.payments.filter(status="pending").first(), "title": f"Pay {inv.number}"})


@customer_required
def profile(request):
    from accounts.forms import CustomerForm, PaymentMethodForm
    from accounts.models import PaymentMethod
    c = _mine(request)
    form = CustomerForm(request.POST or None, instance=c, prefix="p")
    form.fields.pop("email")  # login email changes need verification; not self-service
    mform = PaymentMethodForm(request.POST or None, prefix="m")
    if request.method == "POST":
        if request.POST.get("remove"):
            PaymentMethod.objects.filter(customer=c, pk=request.POST["remove"]).delete()
            return redirect("core:portal_profile")
        if "m-label" in request.POST:
            if mform.is_valid():
                m = mform.save(commit=False)
                m.customer = c
                m.save()
                if m.is_default:
                    c.payment_methods.exclude(pk=m.pk).update(is_default=False)
                messages.success(request, "Payment preference saved.")
                return redirect("core:portal_profile")
        elif form.is_valid():
            form.save()
            audit.log(request.user, "portal.profile", c)
            messages.success(request, "Billing details updated. They apply to your next invoice.")
            return redirect("core:portal_profile")
    return render(request, "portal/profile.html", {"form": form, "mform": mform, "methods": c.payment_methods.all(), "title": "Account"})


@customer_required
def subscription_print(request, pk):
    from subscriptions.views import quotation_ctx
    sub = get_object_or_404(_subs(request).select_related("plan", "customer", "salesperson"), pk=pk)
    return render(request, "documents/quotation.html", quotation_ctx(request, sub))


@customer_required
def statement(request):
    from .views import statement_ctx
    return render(request, "documents/statement.html", statement_ctx(request, _mine(request)))
