import threading
import time
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from billing import engine, providers, services as billing
from billing.models import Invoice, Payment, PaymentEvent, PaymentLink
from core import reports

from .helpers import Base, make_active, make_world


@override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="sek", RAZORPAY_WEBHOOK_SECRET="whsec_test")
class FinancialIntegrity(Base):
    def setUp(self):
        super().setUp()
        sub = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        self.inv = Invoice.objects.get(subscription=sub)
        billing.confirm_invoice(self.inv)

    def test_failed_webhook_effect_does_not_consume_event_id(self):
        link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        event = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link.provider_id}},
                 "payment": {"entity": {"id": "pay_1", "amount": int(self.inv.total * 100)}}}}
        with mock.patch.object(providers.payments, "record_payment", side_effect=RuntimeError("temporary database failure")):
            with self.assertRaises(RuntimeError):
                providers.handle_event("evt_retry", event)
        self.assertFalse(PaymentEvent.objects.filter(provider="razorpay", event_id="evt_retry").exists())
        self.assertEqual(providers.handle_event("evt_retry", event), "recorded")
        self.assertEqual(Payment.objects.filter(provider_ref="pay_1").count(), 1)

    def test_webhook_does_not_truncate_overpayment_to_invoice_balance(self):
        PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        event = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": "plink_1"}},
                 "payment": {"entity": {"id": "pay_over", "amount": int((self.inv.total + 1) * 100)}}}}
        outcome = providers.handle_event("evt_over", event)
        self.inv.refresh_from_db()
        self.assertIn("not applied", outcome)
        self.assertEqual((self.inv.amount_paid, Payment.objects.count()), (Decimal("0.00"), 0))

    def test_webhook_rejects_currency_and_amount_mismatches(self):
        link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        wrong_currency = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link.provider_id}},
                          "payment": {"entity": {"id": "pay_usd", "amount": int(self.inv.total * 100), "currency": "USD"}}}}
        wrong_amount = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link.provider_id}},
                        "payment": {"entity": {"id": "pay_short", "amount": int(self.inv.total * 100) - 1, "currency": "INR"}}}}
        self.assertIn("currency", providers.handle_event("evt_usd", wrong_currency))
        self.assertIn("does not match", providers.handle_event("evt_short", wrong_amount))
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.amount_paid, Payment.objects.count()), (Decimal("0.00"), 0))

    def test_payment_captured_event_resolves_its_link_from_payment_entity(self):
        link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        event = {"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_captured", "payment_link_id": link.provider_id, "amount": int(self.inv.total * 100), "currency": "INR"
        }}}}
        self.assertEqual(providers.handle_event("evt_captured", event), "recorded")
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.amount_paid, Payment.objects.get().provider_ref), (self.inv.total, "pay_captured"))

    def test_link_reconciliation_rejects_provider_receipt_mismatches(self):
        link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        receipts = [
            {"payment_id": "pay_usd", "status": "captured", "amount": int(self.inv.total * 100), "currency": "USD"},
            {"payment_id": "pay_short", "status": "captured", "amount": int(self.inv.total * 100) - 1, "currency": "INR"},
        ]
        for index, receipt in enumerate(receipts):
            with self.subTest(receipt=receipt), mock.patch.object(providers, "_call", return_value={"status": "paid", "payments": [receipt]}):
                with self.assertRaises(billing.BillingError):
                    providers.reconcile_link(link)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(RAZORPAY_KEY_ID="rzp_live_mock", RAZORPAY_KEY_SECRET="sek")
    def test_live_provider_receipts_must_include_currency(self):
        link = PaymentLink.objects.create(invoice=self.inv, provider="razorpay", provider_id="plink_1", url="https://rzp.io/x", amount=self.inv.total)
        event = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link.provider_id}},
                 "payment": {"entity": {"id": "pay_missing_currency", "amount": int(self.inv.total * 100)}}}}
        self.assertIn("currency", providers.handle_event("evt_missing_currency", event))
        with mock.patch.object(providers, "_call", return_value={"status": "paid", "payments": [
            {"payment_id": "pay_missing_currency_reconcile", "status": "captured", "amount": int(self.inv.total * 100)}
        ]}):
            with self.assertRaises(billing.BillingError):
                providers.reconcile_link(link)
        self.assertEqual(Payment.objects.count(), 0)

    def test_idempotency_key_cannot_replay_a_payment_on_another_invoice(self):
        billing.record_payment(self.inv, amount=Decimal("10"), method="bank", idempotency_key="shared-key")
        other_plan, other_customer = make_world()
        other_sub = make_active(other_plan, other_customer, self.today)
        billing.run_billing(self.today, only=[other_sub.pk])
        other_inv = Invoice.objects.get(subscription=other_sub)
        billing.confirm_invoice(other_inv)
        with self.assertRaises(billing.BillingError):
            billing.record_payment(other_inv, amount=Decimal("10"), method="bank", idempotency_key="shared-key")
        self.assertEqual(Payment.objects.count(), 1)

    def test_payment_idempotency_rejects_changed_amount_and_refund_key_reuse(self):
        billing.record_payment(self.inv, amount=Decimal("10"), method="bank", idempotency_key="amount-key")
        with self.assertRaises(billing.BillingError):
            billing.record_payment(self.inv, amount=Decimal("11"), method="bank", idempotency_key="amount-key")
        billing.refund_payment(self.inv, amount=Decimal("5"), method="bank", idempotency_key="refund-key")
        with self.assertRaises(billing.BillingError):
            billing.record_payment(self.inv, amount=Decimal("5"), method="bank", idempotency_key="refund-key")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.amount_paid, Decimal("5.00"))

    def test_cancel_waits_for_pending_payment_reconciliation(self):
        pending = billing.report_payment(self.inv, amount=Decimal("10"), method="upi", reference="UTR-1")
        with self.assertRaises(billing.BillingError):
            billing.cancel_invoice(self.inv)
        self.inv.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual((self.inv.state, pending.status), (Invoice.CONFIRMED, Payment.PENDING))

    def test_pending_confirmation_retry_applies_the_report_once(self):
        pending = billing.report_payment(self.inv, amount=Decimal("10"), method="upi", reference="UTR-confirm")
        first = billing.confirm_pending(pending)
        retry = billing.confirm_pending(pending)
        self.inv.refresh_from_db()
        self.assertEqual((first.pk, retry.pk, self.inv.amount_paid), (pending.pk, pending.pk, Decimal("10.00")))
        self.assertEqual(Payment.objects.filter(invoice=self.inv, status=Payment.SUCCEEDED).count(), 1)

    def test_duplicate_customer_reference_returns_one_pending_report(self):
        first = billing.report_payment(self.inv, amount=Decimal("10"), method="upi", reference=" UTR-1 ")
        retry = billing.report_payment(self.inv, amount=Decimal("10"), method="upi", reference="UTR-1")
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(Payment.objects.filter(invoice=self.inv, status=Payment.PENDING).count(), 1)

    def test_finalized_invoice_header_and_lines_are_immutable(self):
        line = self.inv.lines.get()
        self.inv.bill_to_name = "Changed after issue"
        with self.assertRaises(ValidationError):
            self.inv.save(update_fields=["bill_to_name"])
        line.description = "Changed after issue"
        with self.assertRaises(ValidationError):
            line.save(update_fields=["description"])
        with self.assertRaises(ValidationError):
            line.delete()

    def test_refund_retries_are_idempotent_and_sandbox_refunds_stay_sandbox(self):
        billing.record_payment(self.inv, amount=Decimal("100"), method="bank", is_test=True)
        refund = billing.refund_payment(self.inv, amount=Decimal("40"), method="bank", idempotency_key="refund-key")
        retry = billing.refund_payment(self.inv, amount=Decimal("40"), method="bank", idempotency_key="refund-key")
        self.inv.refresh_from_db()
        summary = reports.summary()
        self.assertEqual((refund.pk, retry.pk), (refund.pk, refund.pk))
        self.assertTrue(refund.is_test)
        self.assertEqual((Payment.objects.filter(invoice=self.inv).count(), self.inv.amount_paid), (2, Decimal("60.00")))
        self.assertEqual((summary["cash"], summary["test_cash"]), (Decimal("0.00"), Decimal("60.00")))

    def test_failed_provider_reference_cannot_be_reused_for_another_invoice(self):
        billing.fail_payment(self.inv, amount=Decimal("10"), method="razorpay", reason="declined", provider="razorpay", provider_ref="pay_fail")
        other_plan, other_customer = make_world()
        other_sub = make_active(other_plan, other_customer, self.today)
        billing.run_billing(self.today, only=[other_sub.pk])
        other_inv = Invoice.objects.get(subscription=other_sub)
        with self.assertRaises(billing.BillingError):
            billing.fail_payment(other_inv, amount=Decimal("10"), method="razorpay", reason="declined", provider="razorpay", provider_ref="pay_fail")
        self.assertEqual(Payment.objects.filter(provider_ref="pay_fail").count(), 1)

    def test_row_lock_is_used_when_skip_locked_is_unavailable(self):
        qs, locked, expected = mock.Mock(), mock.Mock(), object()
        qs.select_for_update.return_value = locked
        locked.filter.return_value.first.return_value = expected
        with mock.patch.object(engine.Subscription.objects, "select_related", return_value=qs), \
             mock.patch.object(engine.connection.features, "has_select_for_update", True), \
             mock.patch.object(engine.connection.features, "has_select_for_update_skip_locked", False), \
             mock.patch.object(engine.connection.features, "has_select_for_update_of", True):
            self.assertIs(engine._lock_sub(12), expected)
        qs.select_for_update.assert_called_once_with(of=("self",))

    def test_maintenance_counts_only_fresh_close_transitions(self):
        from datetime import timedelta

        sub = self.inv.subscription
        sub.pending_cancel_at = self.today - timedelta(days=1)
        sub.save(update_fields=["pending_cancel_at"])
        with mock.patch.object(engine.subs, "_finish_close", return_value=False) as finish:
            counts = engine.maintenance(self.today)
        finish.assert_called_once()
        self.assertEqual(counts, (0, 0))


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentIntegrity(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        from django.utils import timezone

        self.today = timezone.localdate()
        self.plan, self.customer = make_world()
        self.sub = make_active(self.plan, self.customer, self.today)
        billing.run_billing(self.today, only=[self.sub.pk])
        self.inv = Invoice.objects.get(subscription=self.sub)
        billing.confirm_invoice(self.inv)

    def run_two(self, work):
        gate = threading.Barrier(3)
        results, errors = [], []

        def runner():
            try:
                gate.wait(timeout=10)
                results.append(work())
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "worker did not finish")
        return results, errors

    def test_concurrent_payment_retries_apply_once(self):
        inv_id = self.inv.pk
        results, errors = self.run_two(lambda: billing.record_payment(Invoice.objects.get(pk=inv_id), amount=100, method="bank", idempotency_key="parallel-key"))
        self.assertEqual(errors, [])
        self.assertEqual(len({payment.pk for payment in results}), 1)
        self.inv.refresh_from_db()
        self.assertEqual((Payment.objects.filter(invoice=self.inv).count(), self.inv.amount_paid), (1, Decimal("100.00")))

    def test_concurrent_credit_notes_cannot_overcredit(self):
        results, errors = self.run_two(lambda: billing.issue_credit_note(self.inv, amount=700))
        self.assertEqual((len(results), len(errors)), (1, 1))
        self.assertIsInstance(errors[0], billing.BillingError)
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.credited_total, self.inv.credit_notes.count()), (Decimal("700.00"), 1))

    def test_concurrent_billing_claims_a_limited_discount_once(self):
        from catalog.models import Discount

        second_plan, second_customer = make_world()
        second = make_active(second_plan, second_customer, self.today)
        discount = Discount.objects.create(name="One use", kind="fixed", value=100, start_date=self.today, usage_limit=1)
        work = lambda sub_id: billing.run_billing(self.today, only=[sub_id])
        errors = []

        def run(thread):
            try:
                thread()
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()
        gate = threading.Barrier(3)

        def run_subscription(sub_id):
            gate.wait(timeout=10)
            run(lambda: work(sub_id))

        workers = [threading.Thread(target=run_subscription, args=(sid,)) for sid in (self.sub.pk, second.pk)]
        for worker in workers:
            worker.start()
        gate.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=30)
        self.assertFalse(any(worker.is_alive() for worker in workers), "billing runner did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(Invoice.objects.filter(subscription__in=(self.sub, second), discount_total__gt=0).count(), 1)
        discount.refresh_from_db()
        self.assertEqual((discount.times_used, discount.uses.count()), (1, 1))

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="sek", RAZORPAY_WEBHOOK_SECRET="whsec_test")
    def test_concurrent_payment_link_requests_create_one_provider_link(self):
        calls = []

        def provider_call(*args, **kwargs):
            time.sleep(0.05)
            calls.append(len(calls) + 1)
            return {"id": f"plink_{calls[-1]}", "short_url": "https://rzp.io/test"}

        inv_id = self.inv.pk
        with mock.patch.object(providers, "_call", side_effect=provider_call):
            results, errors = self.run_two(lambda: providers.create_payment_link(Invoice.objects.get(pk=inv_id)))
        self.assertEqual(errors, [])
        self.assertEqual(len({link.pk for link in results}), 1)
        self.assertEqual((len(calls), PaymentLink.objects.filter(invoice_id=inv_id).count()), (1, 1))
