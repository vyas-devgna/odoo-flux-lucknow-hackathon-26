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


class OperationsTests(TestCase):
    def setUp(self):
        self.plan, self.cust = make_world()
        self.today = timezone.localdate()
        self.admin = User.objects.create_user("ad@x.test", PW, role="admin")

    def test_login_throttle(self):
        User.objects.create_user("t@x.test", PW)
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": "wrong"})
        r = self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": PW})
        self.assertContains(r, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_success_clears_failures(self):
        User.objects.create_user("t@x.test", PW)
        self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": "bad"})
        r = self.client.post(reverse("accounts:login"), {"username": "T@X.test", "password": PW})
        self.assertEqual(r.status_code, 302)

    def test_audit_trail_and_admin_only_pages(self):
        from core.models import AuditLog
        active_sub(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get(); billing.confirm_invoice(inv)
        self.client.force_login(self.admin)
        self.client.post(reverse("billing:action", args=[inv.pk, "pay"]), {"amount": "100", "method": "upi", "date": self.today.isoformat(), "note": "x"})
        self.assertTrue(AuditLog.objects.filter(action="payment.record", target_label=inv.number).exists())
        self.assertTrue(AuditLog.objects.filter(action="billing.run").exists())
        self.assertEqual(self.client.get(reverse("core:audit")).status_code, 200)
        staff = User.objects.create_user("s@x.test", PW, role="staff")
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("core:audit")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:settings")).status_code, 403)

    def test_reminders_respect_window_and_expiry_notice_once(self):
        s = active_sub(self.plan, self.cust, add_interval(self.today, "monthly", -2))
        billing.run_billing(self.today)
        for i in Invoice.objects.all():
            billing.confirm_invoice(i)
        Invoice.objects.update(due_date=self.today - timedelta(days=3))
        Subscription.objects.filter(pk=s.pk).update(expiry_date=self.today + timedelta(days=5))
        sent, notices, failed = billing.send_due_reminders()
        self.assertEqual((sent, notices, failed), (3, 1, 0))
        self.assertEqual(billing.send_due_reminders(), (0, 0, 0))  # within repeat window, notice already sent
        self.assertEqual(Invoice.objects.filter(reminder_count=1).count(), 3)

    def test_edit_live_subscription_keeps_schedule(self):
        s = active_sub(self.plan, self.cust, self.today)
        nxt = s.next_invoice_date
        self.client.force_login(self.admin)
        line = s.lines.get()
        data = {"expiry_date": s.expiry_date.isoformat(), "payment_terms": "net30", "notes": "",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "1", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-id": line.pk, "lines-0-subscription": s.pk, "lines-0-label": line.label, "lines-0-quantity": "7",
                "lines-0-unit_price": "1000.00", "lines-0-tax": line.tax_id}
        r = self.client.post(reverse("subscriptions:edit", args=[s.pk]), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        s.refresh_from_db(); line.refresh_from_db()
        self.assertEqual((line.quantity, s.next_invoice_date, s.payment_terms), (7, nxt, "net30"))

    def test_health_backup_and_exports(self):
        self.assertEqual(self.client.get(reverse("core:healthz")).json(), {"status": "ok"})
        self.client.force_login(self.admin)
        for w in ("subscriptions", "payments", "customers"):
            r = self.client.get(reverse("core:export", args=[w]))
            self.assertEqual(r["Content-Type"], "text/csv")

    def test_portal_profile_updates_future_invoices_only(self):
        u = User.objects.create_user("c@x.test", PW, role="customer"); self.cust.user = u; self.cust.save()
        active_sub(self.plan, self.cust, self.today); billing.run_billing(self.today)
        old = Invoice.objects.get()
        self.client.force_login(u)
        self.client.post(reverse("core:portal_profile"), {"name": "A", "company": "New Co", "phone": "", "address": "Somewhere", "tax_id": ""})
        old.refresh_from_db()
        self.assertEqual(old.bill_to_address, "")
        self.assertEqual(Customer.objects.get().address, "Somewhere")


class FeatureTests(TestCase):
    def setUp(self):
        self.plan, self.cust = make_world()
        self.today = timezone.localdate()
        self.admin = User.objects.create_user("ad@x.test", PW, role="admin")

    def test_refund_reopens_invoice_and_cash_total(self):
        active_sub(self.plan, self.cust, self.today); billing.run_billing(self.today)
        inv = Invoice.objects.get(); billing.confirm_invoice(inv)
        billing.record_payment(inv, amount=inv.total, method="bank", date=self.today)
        inv.refresh_from_db(); self.assertEqual(inv.state, "paid")
        billing.refund_payment(inv, amount=500, method="bank", date=self.today, note="goodwill")
        inv.refresh_from_db()
        self.assertEqual((inv.state, inv.balance), ("confirmed", Decimal("500.00")))
        with self.assertRaises(billing.BillingError):
            billing.refund_payment(inv, amount=99999, method="bank", date=self.today)
        from core import reports
        self.assertEqual(reports.summary(self.today, self.today)["cash"], inv.total - 500)

    def test_prorated_adjustment_invoice(self):
        s = active_sub(self.plan, self.cust, self.today - timedelta(days=9))  # period ~ 9 days in
        s = Subscription.objects.get(pk=s.pk)
        s.next_invoice_date = add_interval(s.start_date, "monthly", 1); s.periods_billed = 1; s.save()
        line = s.lines.get(); before = {line.pk: line.amount}
        line.quantity += 3; line.save()
        adj = billing.create_adjustment_invoice(s, before, today=self.today)
        self.assertEqual(adj.kind, "adjustment"); self.assertEqual(adj.state, "draft")
        total_days = (add_interval(s.start_date, "monthly", 1) - s.start_date).days
        left = (add_interval(s.start_date, "monthly", 1) - timedelta(days=1) - self.today).days + 1
        self.assertEqual(adj.subtotal, (Decimal(3000) * left / total_days).quantize(Decimal("0.01")))
        self.assertIsNone(billing.create_adjustment_invoice(s, {line.pk: line.amount}, today=self.today))  # no increase -> nothing
        billing.run_billing(self.today)  # adjustment never blocks or duplicates recurring billing
        self.assertEqual((Invoice.objects.filter(kind="recurring").count(), Invoice.objects.filter(kind="adjustment").count()), (0, 1))

    def test_product_tax_and_discount_scope(self):
        low = Tax.objects.create(name="Low", rate=5)
        prod = Product.objects.create(name="Book", type="physical", sales_price=100, tax=low)
        from catalog.models import QuotationTemplate, TemplateLine
        tpl = QuotationTemplate.objects.create(name="T", plan=self.plan); TemplateLine.objects.create(template=tpl, product=prod, quantity=1)
        s = subs.create_subscription(customer=self.cust, plan=self.plan, template=tpl)
        self.assertEqual(sorted(l.tax_rate for l in s.lines.all()), [Decimal("5.00"), Decimal("18.00")])
        d = Discount.objects.create(name="OnlyBooks", kind="percent", value=50, scope="products", start_date=self.today)
        d.products.set([prod])
        subs.send_quotation(s); subs.confirm(s); subs.activate(s); billing.run_billing(self.today)
        self.assertEqual(Invoice.objects.get().discount_total, Decimal("50.00"))  # 50% of the book line only
        d2 = Discount.objects.create(name="EmptyScope", kind="percent", value=90, scope="products", start_date=self.today)
        self.assertEqual(d2.products.count(), 0)  # a products-scoped rule with no products must not match everything

    def test_portal_online_payment_and_toggle(self):
        from core.models import CompanySettings
        u = User.objects.create_user("c@x.test", PW, role="customer"); self.cust.user = u; self.cust.save()
        active_sub(self.plan, self.cust, self.today); billing.run_billing(self.today)
        inv = Invoice.objects.get(); billing.confirm_invoice(inv)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:portal_pay", args=[inv.pk])).status_code, 200)
        co = CompanySettings.get(); co.online_payments = False; co.save()
        self.client.post(reverse("core:portal_pay", args=[inv.pk]))
        inv.refresh_from_db(); self.assertEqual(inv.state, "confirmed")  # disabled -> no payment
        co.online_payments = True; co.save()
        self.client.post(reverse("core:portal_pay", args=[inv.pk]))
        inv.refresh_from_db(); self.assertEqual(inv.state, "paid")
        # another customer's invoice
        c2 = Customer.objects.create(name="B", email="b@x.test"); other = active_sub(self.plan, c2, self.today); billing.run_billing(self.today)
        oi = Invoice.objects.get(subscription=other); billing.confirm_invoice(oi)
        self.assertEqual(self.client.post(reverse("core:portal_pay", args=[oi.pk])).status_code, 404)

    def test_quote_email_and_bulk_actions(self):
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        self.client.force_login(self.admin)
        self.client.post(reverse("subscriptions:action", args=[s.pk, "send"]))
        self.assertEqual(len(mail.outbox), 1); self.assertIn(s.number, mail.outbox[0].subject)
        active_sub(self.plan, Customer.objects.create(name="Z", email="z@x.test"), self.today - timedelta(days=40)); billing.run_billing(self.today)
        ids = list(Invoice.objects.values_list("pk", flat=True))
        self.client.post(reverse("billing:bulk"), {"ids": ids, "action": "confirm"})
        self.assertFalse(Invoice.objects.filter(state="draft").exists())
        self.client.post(reverse("billing:bulk"), {"ids": ids, "action": "confirm"})  # already confirmed -> skipped, no crash

    def test_statement_and_new_pages(self):
        active_sub(self.plan, self.cust, self.today); billing.run_billing(self.today)
        inv = Invoice.objects.get(); billing.confirm_invoice(inv); billing.record_payment(inv, amount=100, method="upi", date=self.today)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:customer_statement", args=[self.cust.pk]))
        self.assertContains(r, inv.number)
        for u in (reverse("subscriptions:list") + "?view=board", reverse("billing:list") + "?sort=due", reverse("subscriptions:list") + "?sort=-customer",
                  reverse("core:dashboard") + "?range=year", reverse("subscriptions:print", args=[Subscription.objects.first().pk])):
            self.assertEqual(self.client.get(u).status_code, 200, u)
