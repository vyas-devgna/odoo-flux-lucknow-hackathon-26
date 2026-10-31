import threading
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Customer
from billing import pricing, services as billing
from billing.models import BillingFailure, BillingPeriod, DiscountUse, Invoice
from catalog.models import Discount, Plan, Product, Tax
from subscriptions import services as subs
from subscriptions.models import Subscription, SubscriptionLine, add_interval

from .helpers import Base, make_active, make_world


class Calendar(Base):
    def test_month_end_anchor_and_leap_years(self):
        a = date(2027, 1, 31)
        self.assertEqual([add_interval(a, "monthly", n) for n in (1, 2, 3, 4)], [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)])
        self.assertEqual(add_interval(date(2028, 1, 31), "monthly", 1), date(2028, 2, 29))
        self.assertEqual(add_interval(date(2028, 2, 29), "yearly", 1), date(2029, 2, 28))
        self.assertEqual(add_interval(date(2028, 2, 29), "yearly", 4), date(2032, 2, 29))
        self.assertEqual(add_interval(date(2026, 12, 31), "daily", 1), date(2027, 1, 1))
        self.assertEqual(add_interval(date(2026, 12, 29), "weekly", 1), date(2027, 1, 5))

    def test_every_frequency_bills_the_right_number_of_periods(self):
        for interval, n_days, expect in (("daily", 5, 6), ("weekly", 15, 3), ("monthly", 70, 3), ("yearly", 800, 3)):
            plan, cust = make_world(interval)
            start = self.today - timedelta(days=n_days)
            s = make_active(plan, cust, start, expiry_date=self.today + timedelta(days=500))
            billing.run_billing(self.today)
            self.assertEqual(Invoice.objects.filter(subscription=s).count(), expect, interval)
            self.assertEqual(billing.run_billing(self.today).created, 0, interval)


class Idempotency(Base):
    def test_rerun_creates_nothing_and_catch_up_is_exact(self):
        s = make_active(self.plan, self.cust, add_interval(self.today, "monthly", -2))
        self.assertEqual(billing.run_billing(self.today).created, 3)
        self.assertEqual(billing.run_billing(self.today).created, 0)
        inv = Invoice.objects.filter(subscription=s).order_by("period_start").first()
        self.assertEqual((inv.subtotal, inv.tax_total, inv.total), (Decimal("1000.00"), Decimal("180.00"), Decimal("1180.00")))

    def test_crash_between_invoice_and_schedule_does_not_duplicate(self):
        s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        Subscription.objects.filter(pk=s.pk).update(periods_billed=0, next_invoice_date=self.today)  # as if the schedule update was lost
        r = billing.run_billing(self.today)
        self.assertEqual((r.created, r.skipped), (0, 1))
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(BillingPeriod.objects.filter(subscription=s).count(), 1)

    def test_database_rejects_duplicate_period_invoice(self):
        from django.db import IntegrityError, transaction
        s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(subscription=s, customer=self.cust, period_start=inv.period_start, period_end=inv.period_end, due_date=self.today, bill_to_name="x", bill_to_email="x@x.test")

    def test_paused_and_scheduled_cancel_not_billed(self):
        s = make_active(self.plan, self.cust, self.today)
        subs.pause(s)
        self.assertEqual(billing.run_billing(self.today).created, 0)
        subs.resume(Subscription.objects.get(pk=s.pk))
        billing.run_billing(self.today)
        s2 = make_active(*make_world(), start=add_interval(self.today, "monthly", -1) + timedelta(days=3))
        billing.run_billing(self.today)
        subs.close(Subscription.objects.get(pk=s2.pk), reason="customer_request")  # period_end policy by default -> scheduled
        s2 = Subscription.objects.get(pk=s2.pk)
        self.assertIsNotNone(s2.pending_cancel_at)
        later = s2.pending_cancel_at + timedelta(days=40)
        billing.run_billing(later)
        s2.refresh_from_db()
        self.assertEqual(s2.state, "closed")
        self.assertEqual(Invoice.objects.filter(subscription=s2).count(), 1)  # nothing billed after the end

    def test_failures_are_recorded_then_cleared_on_retry(self):
        s = make_active(self.plan, self.cust, self.today)
        import billing.documents as docs
        original = docs.pricing.price

        def boom(*a, **k):
            raise RuntimeError("pricing exploded")
        docs.pricing.price = boom
        try:
            r = billing.run_billing(self.today)
        finally:
            docs.pricing.price = original
        self.assertEqual(r.failed, 1)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(BillingFailure.objects.filter(subscription=s, resolved_at__isnull=True).count(), 1)
        billing.retry_failures()
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(BillingFailure.objects.filter(resolved_at__isnull=True).count(), 0)


class Concurrency(TransactionTestCase):
    """Two billing runners at once: exactly one invoice per period (needs a file database so threads get real connections)."""

    def test_parallel_runs_do_not_duplicate(self):
        plan, cust = make_world()
        today = timezone.localdate()
        subs_ = [make_active(plan, Customer.objects.create(name=f"C{i}", email=f"c{i}@x.test"), add_interval(today, "monthly", -1)) for i in range(6)]
        errors = []

        def runner():
            try:
                billing.run_billing(today, trigger="thread")
            except Exception as e:  # sqlite may report 'database is locked' for the loser; that is not a duplicate
                errors.append(repr(e))
            finally:
                connection.close()
        ts = [threading.Thread(target=runner) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        billing.run_billing(today)  # finish whatever a locked runner skipped
        for s in subs_:
            self.assertEqual(Invoice.objects.filter(subscription=s, kind="recurring").count(), 2, errors)
            self.assertEqual(BillingPeriod.objects.filter(subscription=s).count(), 2)


class Snapshots(Base):
    def test_invoice_is_frozen_once_confirmed_and_ignores_catalogue_changes(self):
        from django.core.exceptions import ValidationError
        s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        billing.confirm_invoice(inv)
        Plan.objects.filter(pk=self.plan.pk).update(price=9999)
        SubscriptionLine.objects.filter(subscription=s).update(unit_price=9999)
        self.cust.name = "Renamed"
        self.cust.save()
        inv.refresh_from_db()
        self.assertEqual(inv.total, Decimal("1180.00"))
        self.assertEqual(inv.bill_to_name, self.cust.email.split("@")[0] and inv.bill_to_name)
        inv.subtotal = Decimal("1.00")
        with self.assertRaises(ValidationError):
            inv.save()
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal("1000.00"))


class Discounts(Base):
    def bill(self, qty=1, start=None, plan=None):
        s = make_active(plan or self.plan, self.cust, start or self.today, qty=qty)
        billing.run_billing(self.today)
        return s

    def test_percent_and_fixed_then_tax_after_discount(self):
        Discount.objects.create(name="P", kind="percent", value=10, start_date=self.today)
        self.bill(qty=2)
        inv = Invoice.objects.get()
        self.assertEqual((inv.subtotal, inv.discount_total, inv.tax_total, inv.total), (Decimal("2000.00"), Decimal("200.00"), Decimal("324.00"), Decimal("2124.00")))

    def test_min_quantity_and_min_purchase_gate(self):
        Discount.objects.create(name="Vol", kind="fixed", value=100, min_quantity=5, start_date=self.today)
        self.bill(qty=2)
        self.assertEqual(Invoice.objects.get().discount_total, 0)

    def test_expired_exhausted_and_scheduled_never_apply(self):
        Discount.objects.create(name="old", kind="fixed", value=100, start_date=self.today - timedelta(days=50), end_date=self.today - timedelta(days=1))
        Discount.objects.create(name="future", kind="fixed", value=100, start_date=self.today + timedelta(days=1))
        Discount.objects.create(name="spent", kind="fixed", value=100, start_date=self.today, usage_limit=1, times_used=1)
        self.bill()
        self.assertEqual(Invoice.objects.get().discount_total, 0)

    def test_usage_limit_consumed_once_and_retry_safe(self):
        d = Discount.objects.create(name="once", kind="percent", value=10, start_date=self.today - timedelta(days=5), usage_limit=1)
        s = self.bill(start=add_interval(self.today, "monthly", -1))
        invs = list(Invoice.objects.filter(subscription=s).order_by("period_start"))
        self.assertEqual([i.discount_total > 0 for i in invs], [True, False])
        billing.run_billing(self.today)
        d.refresh_from_db()
        self.assertEqual((d.times_used, DiscountUse.objects.count()), (1, 1))

    def test_product_scope_and_plan_scope(self):
        prod = Product.objects.create(name="Book", type="physical", sales_price=100)
        from catalog.models import QuotationTemplate, TemplateLine
        tpl = QuotationTemplate.objects.create(name="T", plan=self.plan)
        TemplateLine.objects.create(template=tpl, product=prod)
        s = subs.create_subscription(customer=self.cust, plan=self.plan, template=tpl)
        subs.send_quotation(s); subs.accept(Subscription.objects.get(pk=s.pk)); subs.activate(Subscription.objects.get(pk=s.pk))
        d = Discount.objects.create(name="books", kind="percent", value=50, scope="products", start_date=self.today)
        d.products.set([prod])
        other = Plan.objects.create(name="Other", price=1, interval="monthly")
        d2 = Discount.objects.create(name="otherplan", kind="percent", value=90, start_date=self.today)
        d2.plans.set([other])
        Discount.objects.create(name="emptyscope", kind="percent", value=90, scope="products", start_date=self.today)
        billing.run_billing(self.today)
        self.assertEqual(Invoice.objects.get().discount_total, Decimal("50.00"))

    def test_cancel_returns_use_and_replacement_reclaims(self):
        d = Discount.objects.create(name="once", kind="fixed", value=100, start_date=self.today, usage_limit=1)
        self.bill()
        inv = Invoice.objects.get()
        billing.cancel_invoice(inv)
        d.refresh_from_db(); self.assertEqual(d.times_used, 0)
        new = billing.replace_invoice(inv)
        d.refresh_from_db(); self.assertEqual(d.times_used, 1)
        self.assertEqual(new.replaces_id, inv.pk)
        billing.run_billing(self.today)
        self.assertEqual(Invoice.objects.count(), 2)  # cancelled invoices are not auto-recreated


class Taxes(Base):
    def test_inclusive_tax_is_carved_out(self):
        inc = Tax.objects.create(name="Incl", rate=12, inclusive=True)
        prod = Product.objects.create(name="Kit", type="physical", sales_price=1120, tax=inc, recurring=True)
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        SubscriptionLine.objects.create(subscription=s, product=prod, quantity=1, unit_price=1120, tax=inc)
        subs.send_quotation(s); subs.accept(Subscription.objects.get(pk=s.pk)); subs.activate(Subscription.objects.get(pk=s.pk))
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        kit = inv.lines.get(description="Kit")
        self.assertEqual((kit.amount, kit.tax_amount), (Decimal("1000.00"), Decimal("120.00")))
        self.assertEqual(inv.total, Decimal("1180.00") + Decimal("1120.00"))

    def test_one_time_lines_bill_only_first_period(self):
        prod = Product.objects.create(name="Setup", type="service", sales_price=500, recurring=False)
        s = subs.create_subscription(customer=self.cust, plan=self.plan, start_date=add_interval(self.today, "monthly", -1))
        SubscriptionLine.objects.create(subscription=s, product=prod, quantity=1, unit_price=500, tax=Tax.default(), recurring=False)
        subs.send_quotation(s); subs.accept(Subscription.objects.get(pk=s.pk)); subs.activate(Subscription.objects.get(pk=s.pk))
        billing.run_billing(self.today)
        first, second = Invoice.objects.order_by("period_start")
        self.assertTrue(first.lines.filter(description="Setup").exists())
        self.assertFalse(second.lines.filter(description="Setup").exists())
        self.assertEqual(subs.mrr_of(s), Decimal("1000.00"))  # one-time work is not recurring revenue

    def test_tier_pricing(self):
        from catalog.models import PlanTier
        PlanTier.objects.create(plan=self.plan, min_quantity=10, unit_price=800)
        self.assertEqual(self.plan.unit_price_for(9), Decimal("1000.00"))
        self.assertEqual(self.plan.unit_price_for(10), Decimal("800.00"))
        s = make_active(self.plan, self.cust, self.today, qty=12)
        self.assertEqual(s.lines.get().unit_price, Decimal("800.00"))
