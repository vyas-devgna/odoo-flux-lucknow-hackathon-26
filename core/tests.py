from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Customer, User
from billing import services as billing
from billing.models import Invoice
from catalog.models import Discount, Plan, Product, Tax
from subscriptions import services as subs
from subscriptions.models import Subscription, add_interval

PW = "Str0ng!Passw0rd"


def make_world():
    Tax.objects.create(name="GST", rate=18, is_default=True)
    plan = Plan.objects.create(name="Monthly", price=1000, interval="monthly")
    cust = Customer.objects.create(name="A", email="a@x.test")
    return plan, cust


def active_sub(plan, cust, start, qty=2):
    s = subs.create_subscription(customer=cust, plan=plan, start_date=start)
    s.lines.update(quantity=qty)
    subs.send_quotation(s); subs.confirm(s); subs.activate(s)
    return Subscription.objects.get(pk=s.pk)


class AccountTests(TestCase):
    def signup(self, email="n@x.test", pw=PW):
        return self.client.post(reverse("accounts:signup"), {"name": "N", "email": email, "password": pw, "confirm": pw})

    def test_signup_rules(self):
        for bad in ("short1!A", "alllowercase1!", "ALLUPPERCASE1!", "NoSpecial1234"):
            self.assertEqual(self.signup(pw=bad).status_code, 200, bad)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(self.signup().status_code, 302)
        self.client.logout()
        self.assertEqual(self.signup("N@X.test").status_code, 200)  # duplicate, case-insensitive
        self.assertEqual(User.objects.count(), 1)

    def test_password_reset_sends_email(self):
        User.objects.create_user("r@x.test", PW, name="R")
        self.client.post(reverse("accounts:reset"), {"email": "r@x.test"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    def test_roles(self):
        admin = User.objects.create_user("ad@x.test", PW, role="admin")
        staff = User.objects.create_user("st@x.test", PW, role="staff")
        cust = User.objects.create_user("cu@x.test", PW, role="customer")
        Customer.objects.create(name="C", email="cu@x.test", user=cust)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("accounts:team")).status_code, 403)
        self.assertEqual(self.client.post(reverse("catalog:discounts_new"), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse("billing:list")).status_code, 200)
        self.client.force_login(cust)
        self.assertEqual(self.client.get(reverse("billing:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 403)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("accounts:team")).status_code, 200)


class BillingTests(TestCase):
    def setUp(self):
        self.plan, self.cust = make_world()
        self.today = timezone.localdate()

    def test_month_end_anchor(self):
        a = date(2027, 1, 31)
        self.assertEqual([add_interval(a, "monthly", n) for n in (1, 2, 3)], [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)])
        self.assertEqual(add_interval(date(2028, 2, 29), "yearly", 1), date(2029, 2, 28))

    def test_idempotent_and_catch_up(self):
        s = active_sub(self.plan, self.cust, add_interval(self.today, "monthly", -2))
        r1 = billing.run_billing(self.today)
        self.assertEqual(r1.created, 3)  # 3 periods: -2, -1, 0 months
        r2 = billing.run_billing(self.today)
        self.assertEqual((r2.created, r2.skipped), (0, 0))
        self.assertEqual(Invoice.objects.filter(subscription=s).count(), 3)
        inv = Invoice.objects.filter(subscription=s).order_by("period_start").first()
        self.assertEqual(inv.subtotal, Decimal("2000.00"))
        self.assertEqual(inv.tax_total, Decimal("360.00"))
        self.assertEqual(inv.total, Decimal("2360.00"))

    def test_lost_schedule_does_not_duplicate(self):
        s = active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        Subscription.objects.filter(pk=s.pk).update(periods_billed=0, next_invoice_date=self.today)  # simulate crash after invoice
        r = billing.run_billing(self.today)
        self.assertEqual((r.created, r.skipped), (0, 1))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_paused_and_closed_not_billed(self):
        s = active_sub(self.plan, self.cust, self.today)
        subs.pause(s)
        self.assertEqual(billing.run_billing(self.today).created, 0)

    def test_discount_applies_then_exhausts(self):
        d = Discount.objects.create(name="D", kind="percent", value=10, start_date=self.today - timedelta(days=5), usage_limit=1)
        s = active_sub(self.plan, self.cust, add_interval(self.today, "monthly", -1))
        billing.run_billing(self.today)
        first, second = Invoice.objects.filter(subscription=s).order_by("period_start")
        self.assertEqual(first.discount_total, Decimal("200.00"))
        self.assertEqual(first.total, Decimal("2124.00"))  # (2000-200)*1.18
        self.assertEqual(second.discount_total, 0)  # limit of 1 already used
        d.refresh_from_db()
        self.assertEqual(d.times_used, 1)

    def test_expired_discount_ignored(self):
        Discount.objects.create(name="Old", kind="fixed", value=100, start_date=self.today - timedelta(days=50), end_date=self.today - timedelta(days=1))
        active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        self.assertEqual(Invoice.objects.get().discount_total, 0)

    def test_cancel_returns_discount_use(self):
        d = Discount.objects.create(name="D", kind="fixed", value=100, start_date=self.today, usage_limit=1)
        active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        billing.cancel_invoice(inv)
        d.refresh_from_db(); self.assertEqual(d.times_used, 0)
        new = billing.replace_invoice(inv)
        d.refresh_from_db(); self.assertEqual(d.times_used, 1)
        self.assertEqual(new.replaces_id, inv.pk)
        billing.run_billing(self.today)  # cancelled invoice is not auto-recreated
        self.assertEqual(Invoice.objects.count(), 2)

    def test_payments(self):
        active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        with self.assertRaises(billing.BillingError):
            billing.record_payment(inv, amount=10, method="upi", date=self.today)  # still draft
        billing.confirm_invoice(inv)
        billing.record_payment(inv, amount=1000, method="upi", date=self.today)
        inv.refresh_from_db(); self.assertEqual((inv.state, inv.balance), ("confirmed", Decimal("1360.00")))
        with self.assertRaises(billing.BillingError):
            billing.record_payment(inv, amount=5000, method="upi", date=self.today)  # overpayment
        billing.record_payment(inv, amount=1360, method="bank", date=self.today)
        inv.refresh_from_db(); self.assertEqual(inv.state, "paid")

    def test_auto_close_and_plan_rules(self):
        s = active_sub(self.plan, self.cust, add_interval(self.today, "monthly", -14))
        Subscription.objects.filter(pk=s.pk).update(expiry_date=add_interval(self.today, "monthly", -2))
        run = billing.run_billing(self.today)
        s.refresh_from_db()
        self.assertEqual(s.state, "closed"); self.assertEqual(run.closed, 1)
        p2 = Plan.objects.create(name="Locked", price=1, interval="monthly", allow_pause=False, allow_early_close=False)
        s2 = active_sub(p2, self.cust, self.today)
        with self.assertRaises(subs.TransitionError): subs.pause(s2)
        with self.assertRaises(subs.TransitionError): subs.close(s2)

    def test_send_invoice_email(self):
        active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        billing.send_invoice(inv)
        inv.refresh_from_db()
        self.assertEqual((inv.state, len(mail.outbox)), ("confirmed", 1))


class PortalIsolationTests(TestCase):
    def test_customer_cannot_open_other_records(self):
        plan, c1 = make_world()
        c2 = Customer.objects.create(name="B", email="b@x.test")
        u = User.objects.create_user("a@x.test", PW, role="customer"); c1.user = u; c1.save()
        mine = active_sub(plan, c1, timezone.localdate()); theirs = active_sub(plan, c2, timezone.localdate())
        billing.run_billing()
        theirs_inv = Invoice.objects.get(subscription=theirs); billing.confirm_invoice(theirs_inv)
        my_inv = Invoice.objects.get(subscription=mine); billing.confirm_invoice(my_inv)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:portal_subscription", args=[mine.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:portal_subscription", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:portal_invoice", args=[theirs_inv.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:portal_invoice", args=[my_inv.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("core:portal_action", args=[theirs.pk, "close"])).status_code, 404)

    def test_addon_toggle_applies_next_invoice(self):
        plan, c = make_world()
        addon = Product.objects.create(name="Pack", type="addon", sales_price=300)
        plan.products.add(addon)
        s = active_sub(plan, c, timezone.localdate())
        subs.set_addon(s, addon.pk, True)
        self.assertEqual(s.lines.count(), 2)
        subs.set_addon(s, addon.pk, False)
        self.assertEqual(s.lines.count(), 1)


class SmokeTests(TestCase):
    def test_every_page_renders_for_staff_and_portal(self):
        from django.core.management import call_command
        call_command("seed_demo", verbosity=0)
        self.client.force_login(User.objects.get(email="admin@brightloop.test"))
        sub = Subscription.objects.filter(state="active").first()
        inv = Invoice.objects.first()
        urls = [reverse("core:dashboard"), reverse("core:reports"), reverse("core:customers"), reverse("subscriptions:list"),
                reverse("subscriptions:new"), reverse("subscriptions:detail", args=[sub.pk]), reverse("billing:list"),
                reverse("billing:detail", args=[inv.pk]), reverse("billing:print", args=[inv.pk]), reverse("billing:payments"),
                reverse("billing:runs"), reverse("accounts:team"), reverse("accounts:team_new")]
        for r in ("products", "plans", "taxes", "templates", "discounts"):
            urls += [reverse(f"catalog:{r}_list"), reverse(f"catalog:{r}_new")]
        urls.append(reverse("catalog:products_edit", args=[Product.objects.first().pk]))
        draft = Subscription.objects.filter(state="draft").first()
        urls.append(reverse("subscriptions:edit", args=[draft.pk]))
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 200, u)
        self.client.force_login(User.objects.get(email="maya@acme.test"))
        for u in (reverse("core:portal"), reverse("core:portal_subscriptions"), reverse("core:portal_invoices")):
            self.assertEqual(self.client.get(u).status_code, 200, u)
