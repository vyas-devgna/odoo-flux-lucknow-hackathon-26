import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from billing import providers, services as billing
from billing.models import Invoice, Payment, PaymentEvent, PaymentLink
from catalog.models import Plan
from core import mail as outbox
from core.models import Notification, Outbox
from subscriptions import services as subs
from subscriptions.models import Subscription

from .helpers import Base, make_active, make_world

SECRET = "whsec_test"


class Money(Base):
    def setUp(self):
        super().setUp()
        self.s = make_active(self.plan, self.cust, self.today, qty=2)
        billing.run_billing(self.today)
        self.inv = Invoice.objects.get()
        billing.confirm_invoice(self.inv)

    def test_partial_then_full_and_overpayment_rejected(self):
        billing.record_payment(self.inv, amount=1000, method="upi", date=self.today)
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.state, self.inv.balance), ("confirmed", Decimal("1360.00")))
        with self.assertRaises(billing.BillingError):
            billing.record_payment(self.inv, amount=5000, method="upi")
        with self.assertRaises(billing.BillingError):
            billing.record_payment(self.inv, amount=0, method="upi")
        billing.record_payment(self.inv, amount=1360, method="bank")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.state, "paid")
        with self.assertRaises(billing.BillingError):
            billing.record_payment(self.inv, amount=1, method="bank")

    def test_draft_invoice_cannot_receive_payment(self):
        s2 = make_active(*make_world(), start=self.today)
        billing.run_billing(self.today)
        draft = Invoice.objects.filter(state="draft").first()
        with self.assertRaises(billing.BillingError):
            billing.record_payment(draft, amount=10, method="bank")

    def test_idempotency_key_makes_double_submit_harmless(self):
        a = billing.record_payment(self.inv, amount=500, method="bank", idempotency_key="k1")
        b = billing.record_payment(self.inv, amount=500, method="bank", idempotency_key="k1")
        self.assertEqual(a.pk, b.pk)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.amount_paid, Decimal("500.00"))

    def test_customer_report_needs_staff_confirmation(self):
        p = billing.report_payment(self.inv, amount=1180, method="upi", reference="UTR123")
        self.inv.refresh_from_db()
        self.assertEqual((p.status, self.inv.amount_paid), ("pending", 0))
        self.assertTrue(Notification.objects.filter(text__contains="reported a payment").exists())
        with self.assertRaises(billing.BillingError):
            billing.report_payment(self.inv, amount=10, method="upi", reference="")  # a reference is required
        billing.confirm_pending(p, user=self.u["finance"])
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.amount_paid, self.inv.state), (Decimal("1180.00"), "confirmed"))
        billing.reject_pending(billing.report_payment(self.inv, amount=100, method="upi", reference="UTR9"), reason="not found")
        self.assertEqual(Payment.objects.filter(status="failed").count(), 1)
        with self.assertRaises(billing.BillingError):
            billing.confirm_pending(Payment.objects.get(status="failed"))

    def test_failed_payment_flags_subscription_notifies_and_does_not_touch_balance(self):
        billing.fail_payment(self.inv, amount=500, method="razorpay", reason="declined", provider="razorpay", provider_ref="pay_F1")
        self.inv.refresh_from_db()
        self.s.refresh_from_db()
        self.assertEqual((self.inv.amount_paid, self.s.payment_exception), (0, True))
        self.assertTrue(Notification.objects.filter(text__contains="Tell staff").exists())
        self.assertIsNone(billing.fail_payment(self.inv, amount=500, method="razorpay", reason="declined", provider="razorpay", provider_ref="pay_F1"))
        billing.record_payment(self.inv, amount=self.inv.balance, method="bank")
        self.s.refresh_from_db()
        self.assertFalse(self.s.payment_exception)  # cleared once nothing is overdue

    def test_credit_note_and_refund_consistency(self):
        billing.record_payment(self.inv, amount=2360, method="bank")
        cn = billing.issue_credit_note(self.inv, amount=180, reason="goodwill")
        self.inv.refresh_from_db()
        self.assertEqual((cn.total, cn.kind, self.inv.credited_total, self.inv.balance), (Decimal("-180.00"), "credit_note", Decimal("180.00"), Decimal("-180.00")))
        billing.refund_payment(self.inv, amount=180, method="bank", note="credit refund")
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.amount_paid, self.inv.balance, self.inv.state), (Decimal("2180.00"), Decimal("0.00"), "paid"))
        with self.assertRaises(billing.BillingError):
            billing.issue_credit_note(self.inv, amount=5000)
        with self.assertRaises(billing.BillingError):
            billing.refund_payment(self.inv, amount=5000, method="bank")
        with self.assertRaises(billing.BillingError):
            billing.cancel_invoice(self.inv)  # paid invoices can't be cancelled; correct them instead

    def test_credit_note_tax_split_is_proportional(self):
        cn = billing.issue_credit_note(self.inv, amount=590)
        self.assertEqual((cn.subtotal, cn.tax_total, cn.total), (Decimal("-500.00"), Decimal("-90.00"), Decimal("-590.00")))

    def test_pay_to_activate_plan_activates_when_first_invoice_is_paid(self):
        plan, c = make_world(activation="payment")
        s = subs.create_subscription(customer=c, plan=plan)
        subs.send_quotation(s)
        s = subs.accept(Subscription.objects.get(pk=s.pk), signed_name="X")
        self.assertEqual(s.state, "confirmed")
        inv = Invoice.objects.get(subscription=s)
        self.assertEqual(inv.state, "confirmed")
        billing.record_payment(inv, amount=inv.total, method="bank")
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "active")


@override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="sek", RAZORPAY_WEBHOOK_SECRET=SECRET)
class Provider(Base):
    def setUp(self):
        super().setUp()
        make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        self.inv = Invoice.objects.get()
        billing.confirm_invoice(self.inv)
        self.link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)

    def post(self, payload, event_id="evt_1", sig=None):
        body = json.dumps(payload).encode()
        sig = sig if sig is not None else hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post(reverse("billing:razorpay_webhook"), data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID=event_id)

    def paid_event(self, amount=118000):
        return {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": "plink_1"}}, "payment": {"entity": {"id": "pay_1", "amount": amount}}}}

    def test_bad_signature_is_rejected_and_records_nothing(self):
        self.assertEqual(self.post(self.paid_event(), sig="deadbeef").status_code, 400)
        self.assertEqual(self.post(self.paid_event(), sig="").status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)

    def test_valid_event_records_once_even_if_delivered_twice(self):
        self.assertEqual(self.post(self.paid_event()).json()["outcome"], "recorded")
        self.assertEqual(self.post(self.paid_event()).json()["outcome"], "duplicate")
        self.assertEqual(self.post(self.paid_event(), event_id="evt_2").status_code, 200)  # different event id, same payment id
        self.inv.refresh_from_db()
        self.assertEqual((Payment.objects.count(), self.inv.state), (1, "paid"))
        pay = Payment.objects.get()
        self.assertTrue(pay.is_test)  # sandbox keys: recorded, but flagged
        self.assertEqual(PaymentEvent.objects.count(), 2)

    def test_test_payments_are_excluded_from_cash_but_listed(self):
        from core import reports
        self.post(self.paid_event())
        s = reports.summary()
        self.assertEqual((s["cash"], s["test_cash"], s["test_count"]), (0, Decimal("1180.00"), 1))

    def test_failed_event_records_failure_not_revenue(self):
        ev = {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_x", "amount": 118000, "payment_link_id": "plink_1", "error_description": "OTP failed"}}}}
        self.assertEqual(self.post(ev, event_id="evt_f").json()["outcome"], "failure recorded")
        self.assertEqual((Payment.objects.get().status, Invoice.objects.get().amount_paid), ("failed", 0))

    def test_unknown_link_is_ignored(self):
        ev = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": "nope"}}, "payment": {"entity": {"id": "pay_9", "amount": 100}}}}
        self.assertEqual(self.post(ev, event_id="evt_u").json()["outcome"], "unknown link")
        self.assertEqual(Payment.objects.count(), 0)

    def test_create_link_refused_while_a_payment_is_awaiting_confirmation(self):
        billing.report_payment(self.inv, amount=100, method="upi", reference="R1")
        PaymentLink.objects.all().delete()
        with self.assertRaises(billing.BillingError):
            providers.create_payment_link(self.inv)

    def test_create_link_calls_provider_and_reuses_live_link(self):
        PaymentLink.objects.all().delete()
        with mock.patch.object(providers, "_call", return_value={"id": "plink_new", "short_url": "https://rzp.io/new"}) as call:
            a = providers.create_payment_link(self.inv)
            b = providers.create_payment_link(self.inv)
        self.assertEqual((a.pk, b.pk, call.call_count), (a.pk, a.pk, 1))
        self.assertEqual(call.call_args.args[2]["amount"], 118000)  # paise

    def test_provider_outage_leaves_no_state_behind(self):
        PaymentLink.objects.all().delete()
        with mock.patch.object(providers, "_call", side_effect=billing.BillingError("Payment provider is unreachable.")):
            with self.assertRaises(billing.BillingError):
                providers.create_payment_link(self.inv)
        self.assertEqual((PaymentLink.objects.count(), Payment.objects.count()), (0, 0))

    def test_reconcile_records_only_if_provider_says_captured(self):
        with mock.patch.object(providers, "_call", return_value={"status": "created", "payments": []}):
            providers.reconcile_link(self.link)
        self.assertEqual(Payment.objects.count(), 0)
        with mock.patch.object(providers, "_call", return_value={"status": "paid", "payments": [{"payment_id": "pay_7", "status": "captured"}]}):
            providers.reconcile_link(self.link)
            providers.reconcile_link(self.link)
        self.assertEqual(Payment.objects.count(), 1)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_unconfigured_provider_is_labelled_not_faked(self):
        self.assertFalse(providers.configured())
        with self.assertRaises(billing.BillingError):
            providers.create_payment_link(self.inv)
