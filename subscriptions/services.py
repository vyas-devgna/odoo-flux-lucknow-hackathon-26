"""Subscription lifecycle. Every transition is enforced here, on the server, for staff and portal alike."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from catalog.models import Plan, Product, ProductVariant, Tax

from .models import Subscription, SubscriptionLine, add_interval


class TransitionError(Exception):
    pass


def _require(sub, action):
    if not sub.can(action):
        raise TransitionError(f"Cannot {action} a {sub.get_state_display().lower()} subscription on plan “{sub.plan.name}”.")


def create_subscription(*, customer, plan, template=None, start_date=None, expiry_date=None, payment_terms="net15",
                        notes="", user=None):
    start_date = start_date or timezone.localdate()
    validity = template.validity_days if template else 30
    with transaction.atomic():
        sub = Subscription.objects.create(
            customer=customer, plan=plan, template=template, start_date=start_date, payment_terms=payment_terms,
            expiry_date=expiry_date or add_interval(start_date, Plan.YEARLY, 1) - timedelta(days=1), notes=notes,
            quote_valid_until=timezone.localdate() + timedelta(days=validity), created_by=user)
        tax = Tax.default()
        # Plan price pre-fills one base line; products are priced from the catalogue. The plan is not charged twice.
        SubscriptionLine.objects.create(subscription=sub, label=plan.name, quantity=plan.min_quantity,
                                        unit_price=plan.price, tax=tax)
        if template:
            for tl in template.lines.select_related("product", "variant"):
                SubscriptionLine.objects.create(subscription=sub, product=tl.product, variant=tl.variant,
                                                quantity=max(tl.quantity, 1), unit_price=tl.unit_price, tax=tl.product.tax or tax)
    return sub


def send_quotation(sub):
    _require(sub, "send")
    sub.state = Subscription.QUOTATION
    sub.save(update_fields=["state"])


def confirm(sub):
    _require(sub, "confirm")
    if sub.quote_expired:
        raise TransitionError("This quotation has expired. Extend its validity date first.")
    if not sub.lines.exists():
        raise TransitionError("Add at least one line first.")
    sub.state = Subscription.CONFIRMED
    sub.save(update_fields=["state"])


def activate(sub):
    _require(sub, "activate")
    with transaction.atomic():
        sub.state = Subscription.ACTIVE
        sub.activated_at = timezone.now()
        sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
        sub.save(update_fields=["state", "activated_at", "next_invoice_date"])


def pause(sub):
    _require(sub, "pause")
    sub.state = Subscription.PAUSED
    sub.save(update_fields=["state"])


def resume(sub):
    """Billing continues from the next unbilled period; paused time is not back-billed."""
    _require(sub, "resume")
    today = timezone.localdate()
    while add_interval(sub.start_date, sub.plan.interval, sub.periods_billed) < today:
        sub.periods_billed += 1
    sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, sub.periods_billed)
    sub.state = Subscription.ACTIVE
    sub.save(update_fields=["state", "periods_billed", "next_invoice_date"])


def close(sub):
    _require(sub, "close")
    sub.state = Subscription.CLOSED
    sub.closed_at = timezone.now()
    sub.next_invoice_date = None
    sub.save(update_fields=["state", "closed_at", "next_invoice_date"])


def renew(sub):
    """Renewal creates a new linked subscription for the next term; the old record is never reopened."""
    _require(sub, "renew")
    with transaction.atomic():
        start = (sub.expiry_date + timedelta(days=1)) if sub.expiry_date else timezone.localdate()
        new = Subscription.objects.create(
            customer=sub.customer, plan=sub.plan, template=sub.template, state=Subscription.CONFIRMED,
            start_date=start, expiry_date=add_interval(start, Plan.YEARLY, 1) - timedelta(days=1),
            payment_terms=sub.payment_terms, renewed_from=sub, created_by=sub.created_by)
        for l in sub.lines.all():
            SubscriptionLine.objects.create(subscription=new, product=l.product, variant=l.variant, label=l.label,
                                            quantity=l.quantity, unit_price=l.unit_price, tax=l.tax)
        activate(new)
    return new


def set_addon(sub, product_id, on):
    """Switch an add-on on or off. Takes effect from the next invoice; issued invoices are untouched."""
    _require(sub, "addons")
    product = sub.plan.products.filter(type=Product.ADDON, active=True, pk=product_id).first()
    if not product:
        raise TransitionError("That add-on is not offered on this plan.")
    existing = sub.lines.filter(product=product)
    if on and not existing.exists():
        SubscriptionLine.objects.create(subscription=sub, product=product, quantity=1,
                                        unit_price=product.sales_price, tax=product.tax or Tax.default())
    elif not on:
        existing.delete()


def email_quotation(sub, request=None):
    """Email the quotation to the customer with a link to accept it in the portal (or to sign up first)."""
    from django.conf import settings
    from django.core.mail import EmailMessage
    from django.urls import reverse
    from core.models import CompanySettings
    co = CompanySettings.get()
    link = ""
    if request is not None:
        target = "core:portal_subscription" if getattr(sub.customer, "user_id", None) else "accounts:signup"
        args = [sub.pk] if target == "core:portal_subscription" else []
        link = f"\nReview and accept it online: {request.build_absolute_uri(reverse(target, args=args))}\n"
        if target == "accounts:signup":
            link += f"(Create your account with {sub.customer.email} to see it.)\n"
    lines = "\n".join(f"  - {l.description} x {l.quantity} @ {settings.CURRENCY_SYMBOL}{l.unit_price}" for l in sub.lines.all())
    body = (f"Hello {sub.customer.name},\n\nHere is your quotation {sub.number} for the {sub.plan.name} plan "
            f"({sub.plan.get_interval_display().lower()} billing), valid until {sub.quote_valid_until:%d %b %Y}.\n\n{lines}\n\n"
            f"Total per period: {settings.CURRENCY_SYMBOL}{sub.total} (incl. tax){link}\nThank you,\n{co.name}")
    EmailMessage(f"Quotation {sub.number} from {co.name}", body, to=[sub.customer.email],
                 reply_to=[co.email] if co.email else None).send(fail_silently=False)
