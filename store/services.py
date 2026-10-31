import hashlib
import json
import uuid
from decimal import Decimal

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Customer
from catalog.models import Product
from core import audit
from subscriptions import services as contracts
from subscriptions.models import Subscription, SubscriptionLine

from .models import CheckoutReceipt

MAX_QUANTITY = 1_000_000
TOKEN_SALT = "store.checkout"


def offered(plan):
    products = list(plan.products.filter(active=True, published=True).select_related("tax").prefetch_related("variants"))
    return ([p for p in products if p.is_addon], [p for p in products if not p.is_addon])


def selection(plan, source):
    quantity = forms.IntegerField(min_value=plan.min_quantity, max_value=MAX_QUANTITY).clean(source.get("quantity") or plan.min_quantity)
    addons, bundled = offered(plan)
    chosen_addons = [a for a in addons if str(a.pk) in source.getlist("addon")]
    variants = {}
    for product in bundled:
        choices = list(product.variants.all())
        value = source.get(f"variant_{product.pk}") or (str(choices[0].pk) if choices else None)
        if value:
            variant = next((v for v in choices if str(v.pk) == value), None)
            if variant is None:
                raise ValidationError(f"Choose an available variant for {product.name}.")
            variants[product.pk] = variant.pk
    chosen_bundled = [p for p in bundled if source.get(f"bundle_{p.pk}")]
    allowed_bundles = {f"bundle_{p.pk}" for p in bundled}
    if any(key.startswith("bundle_") and source.get(key) and key not in allowed_bundles for key in source):
        raise ValidationError("One of the selected products is no longer available. Refresh this plan and choose again.")
    allowed = {str(a.pk) for a in addons}
    if any(value not in allowed for value in source.getlist("addon")):
        raise ValidationError("One of the selected add-ons is no longer available. Refresh this plan and choose again.")
    return quantity, addons, bundled, chosen_addons, chosen_bundled, variants


def price(plan, quantity, addons, bundled, variants):
    day = timezone.localdate()
    lines = [SubscriptionLine(label=plan.name, quantity=quantity, unit_price=plan.unit_price_for(quantity),
                              tax=contracts._default_tax(day), is_plan_line=True)]
    for product in bundled + addons:
        variant = next((v for v in product.variants.all() if v.pk == variants.get(product.pk)), None)
        unit_price = variant.price if variant else product.sales_price
        if unit_price < 0:
            raise ValidationError(f"The price for {product.name} is unavailable. Please contact us.")
        lines.append(SubscriptionLine(product=product, variant=variant, quantity=1, unit_price=unit_price,
                                      tax=contracts._default_tax(day, product), recurring=product.recurring))
    net = sum((line.net_amount for line in lines), Decimal(0))
    tax = sum((line.tax_amount for line in lines), Decimal(0))
    return lines, net, tax, net + tax


def checkout_token(user, plan):
    return signing.dumps({"key": uuid.uuid4().hex, "user": user.pk, "plan": plan.pk}, salt=TOKEN_SALT)


@transaction.atomic
def purchase(*, user, plan, source, signed_name, ip=None):
    try:
        token = signing.loads(source.get("checkout_token", ""), salt=TOKEN_SALT, max_age=86400)
        key = uuid.UUID(token["key"])
        if token["user"] != user.pk or token["plan"] != plan.pk:
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("This checkout has expired. Refresh the plan and try again.") from None
    # Serialize checkout retries per customer; the unique receipt is a durable second guard.
    customer = Customer.objects.select_for_update().get(pk=user.customer.pk)
    quantity, _, _, addons, bundled, variants = selection(plan, source)
    lines, *_ = price(plan, quantity, addons, bundled, variants)
    fingerprint = hashlib.sha256(json.dumps([
        plan.pk, [(l.product_id, l.variant_id, l.quantity, str(l.unit_price), l.tax_id) for l in lines]
    ]).encode()).hexdigest()
    receipt = CheckoutReceipt.objects.filter(key=key, customer=customer).first()
    if receipt:
        if receipt.fingerprint != fingerprint:
            raise ValidationError("This order was already submitted with different options. Open your subscriptions or refresh to start another order.")
        return receipt.subscription
    sub = contracts.create_subscription(customer=customer, plan=plan, quantity=quantity, user=user,
                                        start_date=timezone.localdate(), notes="Ordered from the storefront")
    for line in lines[1:]:
        line.subscription = sub
        line.save()
    contracts.send_quotation(sub, actor=user)
    sub = contracts.accept(sub, actor=user, signed_name=signed_name, ip=ip, how="Accepted at storefront checkout")
    CheckoutReceipt.objects.create(key=key, customer=customer, subscription=sub, fingerprint=fingerprint)
    audit.log(user, "store.checkout", sub, plan.name)
    return sub
