from decimal import Decimal
from unittest.mock import patch

from catalog.models import Discount, Plan, PlanTier, Product
from billing import services as billing
from billing.models import Invoice
from core import reports
from subscriptions import services as subs
from subscriptions.models import QuoteAcceptance, Subscription, SubscriptionChange

from .helpers import Base, make_active


class ContractIntegrity(Base):
    def quote(self):
        sub = subs.create_subscription(customer=self.cust, plan=self.plan)
        subs.send_quotation(sub)
        return Subscription.objects.get(pk=sub.pk)

    def test_stale_quote_cannot_be_accepted_twice(self):
        first = self.quote()
        stale = Subscription.objects.get(pk=first.pk)
        subs.accept(first, signed_name="Alex")
        with self.assertRaises(subs.TransitionError):
            subs.accept(stale, signed_name="Alex")
        self.assertEqual(QuoteAcceptance.objects.filter(subscription=first).count(), 1)

    def test_acceptance_requires_the_revision_the_customer_reviewed(self):
        sub = self.quote()
        with self.assertRaises(subs.TransitionError):
            subs.accept(sub, signed_name="Alex", expected_revision=sub.revision - 1)
        self.assertFalse(sub.acceptances.exists())

    def test_creation_rejects_invalid_quantity_at_the_service_boundary(self):
        for value in (0, -1, True, "2", 1_000_001):
            with self.subTest(value=value), self.assertRaises(subs.TransitionError):
                subs.create_subscription(customer=self.cust, plan=self.plan, quantity=value)
        self.assertFalse(Subscription.objects.exists())

    def test_stale_activation_does_not_duplicate_recurring_revenue(self):
        sub = subs.accept(self.quote(), signed_name="Alex")
        stale = Subscription.objects.get(pk=sub.pk)
        subs.activate(sub)
        with self.assertRaises(subs.TransitionError):
            subs.activate(stale)
        self.assertEqual(SubscriptionChange.objects.filter(subscription=sub, kind="activated").count(), 1)

    def test_pause_and_its_history_rollback_together(self):
        sub = make_active(self.plan, self.cust)
        with patch("subscriptions.services.record_change", side_effect=RuntimeError("history unavailable")):
            with self.assertRaises(RuntimeError):
                subs.pause(sub)
        sub.refresh_from_db()
        self.assertEqual(sub.state, Subscription.ACTIVE)

    def test_adding_an_option_does_not_reprice_agreed_base_fee(self):
        sub = make_active(self.plan, self.cust)
        addon = Product.objects.create(name="Backup", type="addon", sales_price=100)
        self.plan.products.add(addon)
        self.plan.price = 2500
        self.plan.save()
        pv = subs.preview_change(sub, addons={addon.pk: True})
        self.assertEqual(pv["after_net"], Decimal("1100.00"))
        subs.apply_change(sub, addons={addon.pk: True})
        self.assertEqual(sub.lines.get(is_plan_line=True).unit_price, Decimal("1000.00"))

    def test_editing_paused_contract_does_not_create_active_revenue(self):
        sub = make_active(self.plan, self.cust)
        subs.pause(sub)
        subs.apply_change(Subscription.objects.get(pk=sub.pk), quantity=3)
        self.assertEqual(reports.mrr_at(self.today), Decimal("0.00"))

    def test_stale_active_object_cannot_amend_closed_contract(self):
        sub = make_active(self.plan, self.cust)
        stale = Subscription.objects.get(pk=sub.pk)
        subs.close(sub, immediate=True)
        with self.assertRaises(subs.TransitionError):
            subs.apply_change(stale, quantity=2)

    def test_invalid_amendment_timing_is_rejected(self):
        sub = make_active(self.plan, self.cust)
        with self.assertRaises(subs.TransitionError):
            subs.apply_change(sub, quantity=2, effective="yesterday")

    def test_stale_renewal_request_cannot_create_a_second_term(self):
        sub = make_active(self.plan, self.cust)
        stale = Subscription.objects.get(pk=sub.pk)
        subs.renew(sub)
        with self.assertRaises(subs.TransitionError):
            subs.renew(stale)
        self.assertEqual(sub.renewals.count(), 1)

    def test_internal_closure_is_idempotent_and_keeps_the_reason(self):
        sub = make_active(self.plan, self.cust)
        sub.close_reason = "expired"
        self.assertTrue(subs._finish_close(sub))
        self.assertFalse(subs._finish_close(sub))
        sub.refresh_from_db()
        self.assertEqual(sub.close_reason, "expired")
        self.assertEqual(sub.changes.filter(kind="closed").count(), 1)

    def test_immediate_closure_keeps_the_customer_note_and_returns_current_state(self):
        sub = make_active(self.plan, self.cust)
        closed = subs.close(sub, immediate=True, reason="other", note="Project finished")
        self.assertEqual((closed.state, closed.close_note), (Subscription.CLOSED, "Project finished"))

    def test_prefetched_plan_tiers_need_no_per_plan_price_query(self):
        PlanTier.objects.create(plan=self.plan, min_quantity=10, unit_price=800)
        plan = Plan.objects.prefetch_related("tiers").get(pk=self.plan.pk)
        with self.assertNumQueries(0):
            self.assertEqual(plan.unit_price_for(10), Decimal("800.00"))
            self.assertEqual(plan.unit_price_for(1), Decimal("1000.00"))

    def test_billing_frequency_labels_are_explicit_singular_units(self):
        for frequency, unit in (("daily", "day"), ("weekly", "week"), ("monthly", "month"), ("yearly", "year")):
            self.plan.interval = frequency
            self.assertEqual(self.plan.billing_unit, unit)

    def test_downgrade_reverses_a_confirmed_upgrade_without_silently_capping_credit(self):
        sub = make_active(self.plan, self.cust)
        billing.run_billing(self.today)
        billing.confirm_invoice(Invoice.objects.get(subscription=sub))
        upgrade = subs.apply_change(sub, quantity=10)["document"]
        billing.confirm_invoice(upgrade)
        preview = subs.preview_change(sub, quantity=1)
        subs.apply_change(sub, quantity=1)
        credits = sub.invoices.filter(kind=Invoice.CREDIT_NOTE)
        self.assertEqual(sum((-invoice.total for invoice in credits), Decimal(0)), preview["credit"])

    def test_unissued_upgrade_cannot_be_reversed_by_crediting_the_original_invoice(self):
        sub = make_active(self.plan, self.cust, qty=10)
        billing.run_billing(self.today)
        billing.confirm_invoice(Invoice.objects.get(subscription=sub))
        subs.apply_change(sub, quantity=11)
        with self.assertRaisesMessage(subs.TransitionError, "pending adjustment"):
            subs.apply_change(sub, quantity=10)
        self.assertEqual(sub.lines.get().quantity, 11)
        self.assertFalse(sub.invoices.filter(kind=Invoice.CREDIT_NOTE).exists())
        self.assertIsNotNone(subs.apply_change(sub, quantity=10, effective="next")["scheduled_for"])

    def test_discounted_period_requires_next_period_change_instead_of_overstated_credit(self):
        sub = make_active(self.plan, self.cust, qty=2)
        Discount.objects.create(name="Agreed discount", kind="percent", value=20, start_date=self.today)
        billing.run_billing(self.today)
        billing.confirm_invoice(Invoice.objects.get(subscription=sub))
        with self.assertRaisesMessage(subs.TransitionError, "discount"):
            subs.preview_change(sub, quantity=1)
        self.assertIsNotNone(subs.apply_change(sub, quantity=1, effective="next")["scheduled_for"])
