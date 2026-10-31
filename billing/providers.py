"""Optional hosted-checkout provider (Razorpay). Server-side only: we never see card data, and money is recorded
only from signature-verified webhooks or an explicit reconcile call, never from a browser redirect."""
import base64
import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse

from .documents import BillingError
from .models import Invoice, Payment, PaymentEvent, PaymentLink
from . import payments

log = logging.getLogger(__name__)
API = "https://api.razorpay.com/v1"


def _cfg(name):
    return getattr(settings, name, "") or os.environ.get(name, "")


def configured():
    return bool(_cfg("RAZORPAY_KEY_ID") and _cfg("RAZORPAY_KEY_SECRET"))


def webhook_configured():
    return bool(_cfg("RAZORPAY_WEBHOOK_SECRET"))


def is_test_mode():
    return _cfg("RAZORPAY_KEY_ID").startswith("rzp_test_")


def _call(method, path, payload=None):
    auth = base64.b64encode(f"{_cfg('RAZORPAY_KEY_ID')}:{_cfg('RAZORPAY_KEY_SECRET')}".encode()).decode()
    req = urllib.request.Request(API + path, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310: fixed https API base, never user-supplied
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise BillingError(f"Payment provider rejected the request ({e.code}).")
    except Exception as e:  # timeouts etc: outcome unknown, nothing is recorded
        raise BillingError("Payment provider is unreachable. Nothing was charged; please try again shortly.") from e


@transaction.atomic
def create_payment_link(inv):
    """Hosted checkout for the invoice balance. Reuses a live link for the same amount instead of creating duplicates."""
    if not configured():
        raise BillingError("Online payment isn't configured.")
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.state != Invoice.CONFIRMED or inv.balance <= 0:
        raise BillingError("This invoice has nothing left to pay.")
    if inv.payments.filter(status=Payment.PENDING).exists():
        raise BillingError("A payment for this invoice is awaiting confirmation. Please wait for it to be reconciled.")
    live = inv.payment_links.filter(status="created", amount=inv.balance).first()
    if live:
        return live
    body = {"amount": int((inv.balance * 100).to_integral_value()), "currency": "INR", "reference_id": f"{inv.number}-{inv.payment_links.count() + 1}",
            "description": f"Invoice {inv.number}", "customer": {"name": inv.bill_to_name, "email": inv.bill_to_email},
            "notify": {"sms": False, "email": False}, "reminder_enable": False,
            "callback_url": settings.SITE_URL + reverse("core:portal_invoice", args=[inv.pk]), "callback_method": "get"}
    data = _call("POST", "/payment_links", body)
    return PaymentLink.objects.create(invoice=inv, provider="razorpay", provider_id=data["id"], url=data["short_url"], amount=inv.balance)


def verify_signature(body: bytes, signature: str) -> bool:
    secret = _cfg("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_event(event_id: str, payload: dict) -> str:
    """Process one verified event exactly once. Returns a short outcome string."""
    kind = payload.get("event", "")
    with transaction.atomic():
        try:
            with transaction.atomic():
                ev = PaymentEvent.objects.create(provider="razorpay", event_id=event_id, kind=kind)
        except IntegrityError:
            if PaymentEvent.objects.filter(provider="razorpay", event_id=event_id).exists():
                return "duplicate"
            raise
        outcome = "ignored"
        ent = payload.get("payload", {})
        if kind in ("payment_link.paid", "payment.captured", "order.paid"):
            link_e, pay_e = ent.get("payment_link", {}).get("entity", {}), ent.get("payment", {}).get("entity", {})
            link_id = link_e.get("id") or pay_e.get("payment_link_id", "")
            link = PaymentLink.objects.filter(provider="razorpay", provider_id=link_id).select_related("invoice").first()
            if link and pay_e.get("id"):
                try:
                    minor_amount = Decimal(pay_e["amount"])
                    if minor_amount != minor_amount.to_integral_value() or minor_amount <= 0:
                        raise InvalidOperation
                    amount = minor_amount / 100
                except (KeyError, InvalidOperation, TypeError, ValueError):
                    outcome = "not applied: invalid provider amount"
                else:
                    currency_value = pay_e.get("currency")
                    if currency_value is None and is_test_mode():  # Older sandbox fixtures omit this provider field.
                        currency_value = "INR"
                    currency = str(currency_value or "").upper()
                    if currency != "INR":
                        outcome = f"not applied: unsupported provider currency {currency[:12]}"
                    elif amount != link.amount:
                        outcome = "not applied: captured amount does not match payment link"
                    else:
                        try:
                            payments.record_payment(link.invoice, amount=amount, method="razorpay", reference=pay_e["id"], provider="razorpay",
                                                    provider_ref=pay_e["id"], is_test=is_test_mode(), note="Online payment")
                            link.status = "paid"
                            link.save(update_fields=["status"])
                            outcome = "recorded"
                        except BillingError as e:
                            outcome = f"not applied: {e}"
            else:
                outcome = "unknown link"
        elif kind == "payment.failed":
            pe = ent.get("payment", {}).get("entity", {})
            link = PaymentLink.objects.filter(provider="razorpay", provider_id=pe.get("payment_link_id", "")).select_related("invoice").first()
            if link:
                try:
                    failed = payments.fail_payment(link.invoice, amount=Decimal(pe.get("amount", 0)) / 100, method="razorpay",
                                                   reason=pe.get("error_description") or "Payment failed", provider="razorpay",
                                                   provider_ref=pe.get("id", ""), is_test=is_test_mode())
                    outcome = "failure recorded" if failed else "duplicate failure ignored"
                except BillingError as e:
                    outcome = f"not applied: {e}"
        ev.outcome = outcome[:200]
        ev.save(update_fields=["outcome"])
        return outcome


def reconcile_link(link):
    """Ask the provider what happened to a link and record money only if it was actually paid. Safe to repeat."""
    if not configured():
        raise BillingError("Online payment isn't configured.")
    data = _call("GET", f"/payment_links/{link.provider_id}")
    status = data.get("status", "")
    paid = [p for p in data.get("payments", []) or [] if p.get("status") == "captured"]
    if status == "paid" and paid:
        receipt = paid[0]
        pid = receipt.get("payment_id")
        if not pid:
            raise BillingError("Provider reports a capture without a payment reference.")
        currency_value = receipt.get("currency")
        if currency_value is None and is_test_mode():  # Older sandbox fixtures omit this provider field.
            currency_value = "INR"
        currency = str(currency_value or "").upper()
        if currency != "INR":
            raise BillingError(f"Provider reports unsupported currency {currency[:12]}.")
        try:
            amount = Decimal(receipt["amount"]) / 100 if "amount" in receipt else link.amount
        except (InvalidOperation, TypeError, ValueError):
            raise BillingError("Provider reports an invalid captured amount.") from None
        if amount != link.amount:
            raise BillingError("Provider captured amount does not match the payment link.")
        payments.record_payment(link.invoice, amount=amount, method="razorpay", reference=pid,
                                provider="razorpay", provider_ref=pid, is_test=is_test_mode(), note="Online payment (reconciled)")
        link.status = "paid"
        link.save(update_fields=["status"])
    elif status in ("cancelled", "expired"):
        link.status = status
        link.save(update_fields=["status"])
    return status
