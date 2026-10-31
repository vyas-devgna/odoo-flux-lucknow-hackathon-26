from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.utils import timezone

from accounts.models import Customer
from billing import services as billing
from billing.models import Invoice
from catalog.models import Plan, PlanTier, Product, ProductVariant, QuotationTemplate, TemplateLine
from core import mail as outbox
from core.models import Outbox
from subscriptions import services as subs
from subscriptions.models import QuoteAcceptance, ScheduledChange, Subscription, SubscriptionChange, add_interval

from .helpers import Base, make_active, make_world


class Catalogue(Base):
    def test_variant_adds_to_price_and_template_snapshots_it(self):
        suite = Product.objects.create(name="Suite", sales_price=1200)
        prem = ProductVariant.objects.create(product=suite, attribute="Brand", value="Premium", extra_price=560)
        self.assertEqual(prem.price, Decimal("1760.00"))
        tpl = QuotationTemplate.objects.create(name="T", plan=self.plan, validity_days=14)
        TemplateLine.objects.create(template=tpl, product=suite, variant=prem, quantity=2)
        s = subs.create_subscription(customer=self.cust, plan=self.plan, template=tpl)
        line = s.lines.get(product=suite)
        self.assertEqual((line.unit_price, line.quantity, s.quote_valid_until), (Decimal("1760.00"), 2, self.today + timedelta(days=14)))
        suite.sales_price = 5000
        suite.save()
        line.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal("1760.00"))  # agreed prices don't follow catalogue edits

    def test_plan_is_not_charged_twice(self):
        suite = Product.objects.create(name="Suite", sales_price=1200)
        tpl = QuotationTemplate.objects.create(name="T", plan=self.plan)
        TemplateLine.objects.create(template=tpl, product=suite)
        s = subs.create_subscription(customer=self.cust, plan=self.plan, template=tpl)
        self.assertEqual(s.lines.filter(is_plan_line=True).count(), 1)
        self.assertEqual(s.subtotal, Decimal("2200.00"))


class Lifecycle(Base):
    def test_valid_progression_and_invalid_transitions(self):
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        for bad in (subs.confirm, subs.activate, subs.pause, subs.resume):
            with self.assertRaises(subs.TransitionError):
                bad(s)
        subs.send_quotation(s)
        s = Subscription.objects.get(pk=s.pk)
        with self.assertRaises(subs.TransitionError):
            subs.send_quotation(s)
        s = subs.accept(s, actor=self.u["sales"], signed_name="Alex")
        self.assertEqual(s.state, "confirmed")
        self.assertEqual(QuoteAcceptance.objects.filter(subscription=s).count(), 1)
        subs.activate(s)
        s.refresh_from_db()
        self.assertEqual((s.state, s.next_invoice_date), ("active", s.start_date))
        subs.close(s, reason="customer_request", immediate=True)
        s.refresh_from_db()
        self.assertEqual(s.state, "closed")
        with self.assertRaises(subs.TransitionError):
            subs.pause(s)

    def test_quotation_expiry_blocks_acceptance_and_maintenance_cancels(self):
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        subs.send_quotation(s)
        Subscription.objects.filter(pk=s.pk).update(quote_valid_until=self.today - timedelta(days=1))
        with self.assertRaises(subs.TransitionError):
            subs.accept(Subscription.objects.get(pk=s.pk))
        billing.run_billing(self.today)
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "cancelled")

    def test_revision_and_alternatives(self):
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        subs.send_quotation(s)
        subs.revise_quotation(Subscription.objects.get(pk=s.pk))
        self.assertEqual(Subscription.objects.get(pk=s.pk).revision, 2)
        subs.send_quotation(Subscription.objects.get(pk=s.pk))
        alt = subs.clone_as_quote(Subscription.objects.get(pk=s.pk))
        subs.send_quotation(alt)
        subs.accept(Subscription.objects.get(pk=alt.pk))
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "cancelled")  # the sibling is superseded
        self.assertEqual(Subscription.objects.get(pk=alt.pk).state, "confirmed")

    def test_plan_controls_are_enforced_server_side(self):
        locked, c2 = make_world(allow_pause=False, allow_early_close=False, allow_renew=False)
        s = make_active(locked, c2, self.today)
        for action in (subs.pause, subs.close, subs.renew):
            with self.assertRaises(subs.TransitionError):
                action(s)

    def test_customer_cannot_cancel_inside_minimum_term_but_staff_can(self):
        plan, c = make_world(min_duration_periods=3, cancel_policy="anytime")
        s = make_active(plan, c, self.today)
        with self.assertRaises(subs.TransitionError):
            subs.close(s, by_customer=True)
        subs.close(s, actor=self.u["admin"], reason="other")
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "closed")

    def test_period_end_cancellation_is_scheduled_and_can_be_withdrawn(self):
        s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        subs.close(Subscription.objects.get(pk=s.pk), reason="too_expensive")
        s = Subscription.objects.get(pk=s.pk)
        self.assertEqual((s.state, s.pending_cancel_at), ("active", s.next_invoice_date - timedelta(days=1)))
        subs.keep(s)
        self.assertIsNone(Subscription.objects.get(pk=s.pk).pending_cancel_at)

    def test_renewal_quote_then_accept_and_auto_renew(self):
        s = make_active(self.plan, self.cust, add_interval(self.today, "monthly", -11) )
        Subscription.objects.filter(pk=s.pk).update(expiry_date=self.today + timedelta(days=10))
        q = subs.renew(Subscription.objects.get(pk=s.pk), accept=False)
        self.assertEqual((q.state, q.renewed_from_id), ("quotation", s.pk))
        with self.assertRaises(subs.TransitionError):
            subs.renew(Subscription.objects.get(pk=s.pk), accept=False)  # only one renewal per term
        auto, c2 = make_world(renewal_policy="auto")
        a = make_active(auto, c2, add_interval(self.today, "yearly", -1) + timedelta(days=1) - timedelta(days=2))
        Subscription.objects.filter(pk=a.pk).update(expiry_date=self.today - timedelta(days=1), next_invoice_date=add_interval(self.today, "yearly", 5))
        r = billing.run_billing(self.today)
        a.refresh_from_db()
        self.assertEqual((r.renewed, a.state), (1, "closed"))
        new = a.renewals.get()
        self.assertEqual((new.state, new.start_date), ("active", a.expiry_date + timedelta(days=1)))


class Changes(Base):
    def setUp(self):
        super().setUp()
        start = self.today - timedelta(days=9)
        self.sub = make_active(self.plan, self.cust, start, qty=2)
        billing.run_billing(self.today)
        billing.confirm_invoice(Invoice.objects.get(subscription=self.sub))
        self.sub = Subscription.objects.get(pk=self.sub.pk)
        self.p_end = add_interval(start, "monthly", 1) - timedelta(days=1)
        self.days_total = (add_interval(start, "monthly", 1) - start).days
        self.left = (self.p_end - self.today).days + 1

    def test_upgrade_seats_prorates_and_creates_adjustment_invoice(self):
        pv = subs.preview_change(self.sub, quantity=5)
        net = (Decimal(3000) * Decimal(self.left) / Decimal(self.days_total)).quantize(Decimal("0.01"))
        self.assertEqual(pv["adjust_net"], net)
        self.assertEqual(pv["adjust_total"], net + (net * Decimal("0.18")).quantize(Decimal("0.01")))
        self.assertEqual((pv["before_total"], pv["after_total"]), (Decimal("2360.00"), Decimal("5900.00")))
        self.assertEqual((pv["mrr_before"], pv["mrr_after"]), (Decimal("2000.00"), Decimal("5000.00")))
        res = subs.apply_change(self.sub, quantity=5, effective="now")
        doc = res["document"]
        self.assertEqual((doc.kind, doc.state, doc.total), ("adjustment", "draft", pv["adjust_total"]))
        self.assertEqual(Subscription.objects.get(pk=self.sub.pk).lines.get().quantity, 5)
        ch = SubscriptionChange.objects.filter(subscription=self.sub).order_by("id").last()
        self.assertEqual((ch.movement, ch.delta), ("expansion", Decimal("3000.00")))

    def test_downgrade_issues_credit_note_against_current_invoice(self):
        s = make_active(*make_world(), start=self.today - timedelta(days=3), qty=4)
        billing.run_billing(self.today)
        inv = Invoice.objects.get(subscription=s)
        billing.confirm_invoice(inv)
        res = subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=2, effective="now")
        cn = res["document"]
        self.assertEqual((cn.kind, cn.credit_for_id, cn.total < 0), ("credit_note", inv.pk, True))
        inv.refresh_from_db()
        self.assertEqual(inv.credited_total, -cn.total)
        self.assertEqual(inv.total, Decimal("4720.00"))  # the original is untouched

    def test_next_period_change_is_scheduled_then_applied_by_billing(self):
        res = subs.apply_change(self.sub, quantity=4, effective="next")
        self.assertIsNotNone(res["scheduled_for"])
        self.assertEqual(Subscription.objects.get(pk=self.sub.pk).lines.get().quantity, 2)
        self.assertEqual(ScheduledChange.objects.filter(applied_at__isnull=True).count(), 1)
        billing.run_billing(res["scheduled_for"])
        inv = Invoice.objects.filter(subscription=self.sub, period_start=res["scheduled_for"]).get()
        self.assertEqual(inv.subtotal, Decimal("4000.00"))
        self.assertEqual(ScheduledChange.objects.filter(applied_at__isnull=False).count(), 1)

    def test_addon_toggle_and_tier_repricing(self):
        addon = Product.objects.create(name="Pack", type="addon", sales_price=300)
        self.plan.products.add(addon)
        PlanTier.objects.create(plan=self.plan, min_quantity=10, unit_price=800)
        pv = subs.preview_change(self.sub, quantity=10, addons={addon.pk: True})
        self.assertEqual(pv["addons_added"], ["Pack"])
        self.assertEqual(pv["after_net"], Decimal("8300.00"))  # 10 x 800 + 300
        with self.assertRaises(subs.TransitionError):
            subs.preview_change(self.sub, addons={999999: True})

    def test_invalid_changes_are_rejected(self):
        with self.assertRaises(subs.TransitionError):
            subs.apply_change(self.sub, quantity=2)  # nothing to change
        other, _ = make_world("yearly")
        with self.assertRaises(subs.TransitionError):
            subs.preview_change(self.sub, plan=other)  # different frequency
        big, _ = make_world(min_quantity=5)
        with self.assertRaises(subs.TransitionError):
            subs.preview_change(self.sub, plan=big, quantity=2)

    def test_no_retroactive_change_to_issued_invoices(self):
        s = make_active(*make_world(), start=self.today - timedelta(days=2), qty=2)
        billing.run_billing(self.today)
        inv = Invoice.objects.get(subscription=s)
        total = inv.total
        subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=6)
        inv.refresh_from_db()
        self.assertEqual(inv.total, total)


class MrrInvariant(Base):
    def test_movement_based_mrr_equals_line_based_mrr_through_a_life(self):
        from core import reports
        s = make_active(self.plan, self.cust, self.today - timedelta(days=20), qty=3)
        billing.run_billing(self.today)
        subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=5)
        subs.pause(Subscription.objects.get(pk=s.pk))
        self.assertEqual(reports.mrr_at(self.today), 0)
        subs.resume(Subscription.objects.get(pk=s.pk))
        subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=4, effective="now")
        live = sum(x.recurring_monthly for x in Subscription.objects.filter(state="active"))
        self.assertEqual(reports.mrr_at(self.today), live)
        subs.close(Subscription.objects.get(pk=s.pk), reason="other", immediate=True)
        self.assertEqual(reports.mrr_at(self.today), 0)

    def test_history_is_not_rewritten_by_later_changes(self):
        from core import reports
        s = make_active(self.plan, self.cust, self.today - timedelta(days=60), qty=2)
        past = self.today - timedelta(days=30)
        before = reports.mrr_at(past)
        subs.close(Subscription.objects.get(pk=s.pk), reason="other", immediate=True)
        self.assertEqual(reports.mrr_at(past), before)  # still what it was a month ago
        self.assertEqual(before, Decimal("2000.00"))

    def test_daily_and_yearly_mrr_normalisation(self):
        d, c1 = make_world("daily", 10)
        y, c2 = make_world("yearly", 12000)
        sd, sy = make_active(d, c1, self.today), make_active(y, c2, self.today)
        self.assertAlmostEqual(float(sy.recurring_monthly), 12000 * 0.083333, places=1)
        self.assertEqual(sd.recurring_monthly, Decimal("304.38"))
