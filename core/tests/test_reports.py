from datetime import timedelta
from decimal import Decimal

from accounts.models import Customer
from billing import services as billing
from billing.models import Invoice, Payment
from core import reports
from subscriptions import services as subs
from subscriptions.models import Subscription, add_interval

from .helpers import Base, make_active, make_world


class Definitions(Base):
    def setUp(self):
        super().setUp()
        self.s = make_active(self.plan, self.cust, self.today - timedelta(days=40), qty=2)
        billing.run_billing(self.today)
        for i in Invoice.objects.all():
            billing.confirm_invoice(i)
        self.i1, self.i2 = Invoice.objects.order_by("period_start")

    def test_cash_excludes_failed_pending_and_test_and_nets_refunds(self):
        billing.record_payment(self.i1, amount=1000, method="bank", date=self.today)
        billing.record_payment(self.i2, amount=500, method="upi", date=self.today, provider="razorpay", provider_ref="p1", is_test=True)
        billing.report_payment(self.i2, amount=100, method="upi", reference="r")
        billing.fail_payment(self.i2, amount=100, method="razorpay", reason="x")
        billing.refund_payment(self.i1, amount=200, method="bank", date=self.today)
        s = reports.summary(self.today - timedelta(days=1), self.today)
        self.assertEqual((s["cash"], s["test_cash"], s["refunds"], s["payments"]), (Decimal("800.00"), Decimal("500.00"), Decimal("200.00"), 1))

    def test_invoiced_is_net_of_credit_notes_and_outstanding_excludes_them(self):
        before = reports.summary(self.today - timedelta(days=60), self.today)
        billing.issue_credit_note(self.i1, amount=236)
        s = reports.summary(self.today - timedelta(days=60), self.today)
        self.assertEqual(s["invoiced"], before["invoiced"] - Decimal("236.00"))
        self.assertEqual(s["credited"], Decimal("236.00"))
        self.assertEqual(s["outstanding"], before["outstanding"] - Decimal("236.00"))
        self.assertEqual(s["outstanding_count"], 2)

    def test_overdue_and_aging_buckets(self):
        Invoice.objects.filter(pk=self.i1.pk).update(due_date=self.today - timedelta(days=45))
        Invoice.objects.filter(pk=self.i2.pk).update(due_date=self.today + timedelta(days=5))
        s = reports.summary()
        self.assertEqual((s["overdue_count"], s["overdue"]), (1, self.i1.total))
        buckets, total = reports.aging()
        by = {b["label"]: b for b in buckets}
        self.assertEqual((by["31–60 days"]["count"], by["Not yet due"]["count"], total), (1, 1, self.i1.total + self.i2.total))

    def test_revenue_by_dimension_sums_to_invoiced_net(self):
        start = self.today - timedelta(days=60)
        for dim in ("plan", "customer", "product"):
            rows = reports.revenue_by(dim, start, self.today)
            net = sum(r["net"] for r in rows)
            self.assertEqual(net, sum(i.subtotal - i.discount_total for i in Invoice.objects.all()), dim)

    def test_filters_scope_every_number(self):
        other, c2 = make_world("monthly", 500)
        make_active(other, c2, self.today - timedelta(days=40))
        billing.run_billing(self.today)
        f = reports.Filters(plan=self.plan.pk)
        self.assertLess(reports.summary(f=f)["invoiced"], reports.summary()["invoiced"])
        self.assertEqual(reports.mrr_at(self.today, f), Decimal("2000.00"))
        self.assertEqual(reports.mrr_at(self.today), Decimal("2500.00"))
        self.assertEqual(reports.summary(f=reports.Filters(customer=c2.pk))["active"], 1)


class Movements(Base):
    def test_bridge_counts_churn_against_opening_base_only(self):
        old = make_active(self.plan, self.cust, self.today - timedelta(days=200), qty=2)
        c2 = Customer.objects.create(name="Late", email="late@x.test")
        late = make_active(self.plan, c2, self.today - timedelta(days=20), qty=1)
        subs.close(Subscription.objects.get(pk=late.pk), reason="other", immediate=True)  # joined and left inside the window
        subs.close(Subscription.objects.get(pk=old.pk), reason="other", immediate=True)
        br = reports.bridge(self.today - timedelta(days=30), self.today)
        self.assertEqual((br["active_at_start"], br["churned_n"], br["churned_all"]), (1, 1, 2))
        self.assertEqual((br["logo_churn"], br["revenue_churn"]), (Decimal(100), Decimal(100)))  # capped, never 150%
        self.assertEqual(br["closing"], 0)

    def test_series_bridge_adds_up(self):
        s = make_active(self.plan, self.cust, self.today - timedelta(days=75), qty=2)
        subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=4, effective="now")
        rows = reports.mrr_series(6)
        for r in rows:
            self.assertEqual(r["opening"] + r["net"], r["closing"])
        self.assertEqual(rows[-1]["closing"], reports.mrr_at(self.today))
        self.assertEqual(rows[-1]["closing"], Decimal("4000.00"))

    def test_renewal_moves_revenue_without_inflating_new_or_churn(self):
        auto, c = make_world(renewal_policy="auto")
        a = make_active(auto, c, add_interval(self.today, "yearly", -1) + timedelta(days=1) - timedelta(days=2))
        Subscription.objects.filter(pk=a.pk).update(expiry_date=self.today - timedelta(days=1), next_invoice_date=add_interval(self.today, "yearly", 5))
        billing.run_billing(self.today)
        br = reports.bridge(self.today - timedelta(days=5), self.today)
        self.assertEqual((br["cells"].get("new", [0, 0])[1], br["cells"].get("churn", [0, 0])[1]), (0, 0))
        self.assertEqual(br["net"], 0)
        self.assertEqual(reports.mrr_at(self.today), a.renewals.get().recurring_monthly)

    def test_cohort_retention(self):
        a = make_active(self.plan, self.cust, self.today - timedelta(days=95))
        c2 = Customer.objects.create(name="B", email="b@x.test")
        b = make_active(self.plan, c2, self.today - timedelta(days=95))
        subs.close(Subscription.objects.get(pk=b.pk), reason="other", immediate=True)
        co = reports.cohorts(6)
        row = next(r for r in co if r["size"] == 2)
        self.assertEqual(row["cells"][0]["pct"], 100)
        self.assertEqual(row["cells"][-1]["pct"], 50)
