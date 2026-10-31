from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from accounts.models import Customer, User
from billing.models import BillingPeriod, DiscountUse, Invoice, InvoiceLine, Payment
from catalog.models import Discount
from core.management.commands.seed_demo import Command, EXPANSION_PREFIX
from subscriptions.models import QuoteAcceptance, ScheduledChange, Subscription, SubscriptionChange, SubscriptionLine


PROTECTED_MODELS = (Customer, Subscription, SubscriptionLine, SubscriptionChange, QuoteAcceptance,
                    ScheduledChange, Invoice, InvoiceLine, Payment, BillingPeriod, Discount, DiscountUse, User)


def snapshot_protected_rows():
    return {model: list(model.objects.order_by("pk").values()) for model in PROTECTED_MODELS}


class DemoSeedRealismTests(TestCase):
    def test_fresh_seed_has_realistic_volume_and_reconciled_history(self):
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Subscription.objects.count(), 78)
        self.assertEqual(Customer.objects.count(), 38)
        self.assertEqual(Subscription.objects.filter(notes__startswith=EXPANSION_PREFIX).count(), 26)
        self.assertEqual(set(Subscription.objects.values_list("plan__interval", flat=True)), {"daily", "weekly", "monthly", "yearly"})

        new_invoices = Invoice.objects.filter(subscription__notes__startswith=EXPANSION_PREFIX)
        new_payments = Payment.objects.filter(invoice__subscription__notes__startswith=EXPANSION_PREFIX)
        self.assertEqual(new_invoices.count(), 120)  # 118 recurring, one adjustment, one linked credit note
        self.assertEqual(new_payments.count(), 112)  # 100 receipts, 10 failed attempts, one refund, one adjustment receipt
        self.assertEqual(new_invoices.aggregate(total=Sum("discount_total"))["total"] or Decimal("0.00"), Decimal("0.00"))
        self.assertFalse(DiscountUse.objects.filter(invoice__subscription__notes__startswith=EXPANSION_PREFIX).exists())
        self.assertEqual(new_invoices.filter(kind=Invoice.ADJUSTMENT).count(), 1)
        self.assertEqual(new_invoices.filter(kind=Invoice.CREDIT_NOTE).count(), 1)

        today = timezone.localdate()
        for payment in Payment.objects.select_related("invoice"):
            self.assertGreaterEqual(payment.date, payment.invoice.issue_date)
            self.assertLessEqual(payment.date, today)
        for invoice in Invoice.objects.prefetch_related("lines"):
            lines = list(invoice.lines.all())
            self.assertEqual(sum((line.amount for line in lines), Decimal("0.00")), invoice.subtotal - invoice.discount_total)
            self.assertEqual(sum((line.tax_amount for line in lines), Decimal("0.00")), invoice.tax_total)
            succeeded = invoice.payments.filter(status=Payment.SUCCEEDED).aggregate(amount=Sum("amount"))["amount"] or Decimal("0.00")
            self.assertEqual(succeeded, invoice.amount_paid)
        for period in BillingPeriod.objects.filter(subscription__notes__startswith=EXPANSION_PREFIX).select_related("invoice"):
            self.assertEqual(period.invoice.period_start, period.start)
            self.assertEqual(period.invoice.period_end, period.end)
            self.assertEqual(period.invoice.issue_date, period.start)

        credit = new_invoices.get(kind=Invoice.CREDIT_NOTE)
        refund = new_payments.get(amount__lt=0)
        self.assertEqual(credit.credit_for_id, refund.invoice_id)
        self.assertEqual(-refund.amount, credit.total * -1)
        self.assertTrue(SubscriptionLine.objects.filter(variant__isnull=False).exists())
        self.assertTrue(SubscriptionLine.objects.filter(product__type="addon").exists())
        self.assertTrue(Subscription.objects.filter(state=Subscription.QUOTATION).exists())
        self.assertTrue(Payment.objects.filter(status=Payment.FAILED).exists())
        self.assertTrue(Payment.objects.filter(status=Payment.SUCCEEDED, amount__lt=0).exists())
        self.assertTrue(User.objects.get(email="admin@brightloop.test").check_password("Admin@12345"))
        self.assertTrue(User.objects.get(email="maya@acme.test").check_password("Customer@123"))

    def test_expand_is_additive_hash_stable_and_idempotent_with_custom_rows(self):
        Command().build(include_expansion=False)
        custom = Customer.objects.create(name="Morgan Lee", company="Custom Studio", email="owner@custom-studio.test")
        plan = Subscription.objects.first().plan
        for n in range(2):
            Subscription.objects.create(customer=custom, plan=plan, start_date=timezone.localdate() - timedelta(days=30 + n), notes=f"custom contract {n}")
        before = snapshot_protected_rows()

        call_command("seed_demo", expand=True, stdout=StringIO())
        after_first = snapshot_protected_rows()
        for model in PROTECTED_MODELS:
            self.assertEqual(before[model], after_first[model][:len(before[model])])
        self.assertEqual(Subscription.objects.count(), 80)
        self.assertEqual(Subscription.objects.filter(notes__startswith=EXPANSION_PREFIX).count(), 26)
        self.assertEqual(Customer.objects.count(), 39)

        call_command("seed_demo", expand=True, stdout=StringIO())
        self.assertEqual(after_first, snapshot_protected_rows())
        self.assertEqual(Invoice.objects.filter(subscription__notes__startswith=EXPANSION_PREFIX).count(), 120)

    def test_expand_refuses_unrecognized_data_without_writes(self):
        Customer.objects.create(name="Unrelated", company="Outside demo", email="person@outside.test")
        before = snapshot_protected_rows()
        with self.assertRaises(CommandError):
            call_command("seed_demo", expand=True, stdout=StringIO())
        self.assertEqual(before, snapshot_protected_rows())
