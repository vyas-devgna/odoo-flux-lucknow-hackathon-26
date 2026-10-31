from datetime import timedelta

from django.urls import reverse

from accounts.models import Customer, User
from billing import services as billing
from billing.models import Invoice
from subscriptions import services as subs
from subscriptions.models import Subscription

from .helpers import PW, Base, make_active, make_world


class Authorisation(Base):
    def setUp(self):
        super().setUp()
        self.s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        self.inv = Invoice.objects.get()
        billing.confirm_invoice(self.inv)

    def status(self, who, method, url, data=None):
        self.client.force_login(self.u[who])
        return getattr(self.client, method)(url, data or {}).status_code

    def test_capabilities_gate_pages(self):
        reports_url = reverse("core:reports")
        self.assertEqual(self.status("finance", "get", reports_url), 200)
        self.assertEqual(self.status("admin", "get", reports_url), 200)
        for who in ("sales", "billing"):
            self.assertEqual(self.status(who, "get", reports_url), 403, who)
        self.assertEqual(self.status("sales", "get", reverse("billing:list")), 403)
        self.assertEqual(self.status("billing", "get", reverse("billing:list")), 200)
        self.assertEqual(self.status("finance", "get", reverse("billing:list")), 200)
        self.assertEqual(self.status("billing", "get", reverse("subscriptions:new")), 403)
        self.assertEqual(self.status("sales", "get", reverse("subscriptions:new")), 200)

    def test_money_actions_need_finance_even_by_direct_post(self):
        pay = {"amount": "100", "method": "bank", "date": self.today.isoformat()}
        self.assertEqual(self.status("billing", "post", reverse("billing:action", args=[self.inv.pk, "pay"]), pay), 404)
        self.assertEqual(self.status("billing", "post", reverse("billing:quick_pay"), {**pay, "invoice": self.inv.pk}), 403)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.amount_paid, 0)
        self.assertEqual(self.status("finance", "post", reverse("billing:action", args=[self.inv.pk, "pay"]), pay), 302)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.amount_paid, 100)

    def test_billing_actions_need_billing_capability(self):
        self.assertEqual(self.status("finance", "post", reverse("billing:action", args=[self.inv.pk, "send"])), 404)
        self.assertEqual(self.status("finance", "post", reverse("billing:runs")), 404)  # running billing is a billing capability
        self.assertEqual(self.status("billing", "post", reverse("billing:action", args=[self.inv.pk, "send"])), 302)

    def test_staff_without_catalog_cap_cannot_write_catalogue(self):
        self.assertEqual(self.status("sales", "post", reverse("catalog:products_new"), {"name": "x"}), 403)
        self.assertEqual(self.status("sales", "get", reverse("catalog:products_list")), 200)  # read-only visibility
        self.assertEqual(self.status("sales", "post", reverse("catalog:discounts_new"), {}), 403)
        self.assertEqual(self.status("admin", "get", reverse("catalog:discounts_new")), 200)

    def test_admin_only_areas(self):
        for name in ("core:settings", "core:audit", "core:outbox", "core:automation", "accounts:team", "core:settings_payments"):
            for who in ("sales", "billing", "finance"):
                self.assertEqual(self.status(who, "get", reverse(name)), 403, (name, who))
            self.assertEqual(self.status("admin", "get", reverse(name)), 200, name)

    def test_customers_are_locked_out_of_staff_everything(self):
        cu = User.objects.create_user("c@x.test", PW, role="customer")
        Customer.objects.create(name="C", email="c@x.test", user=cu)
        self.client.force_login(cu)
        for name, args in (("core:dashboard", []), ("billing:list", []), ("subscriptions:list", []), ("core:customers", []), ("core:reports", []), ("billing:detail", [self.inv.pk]),
                           ("subscriptions:detail", [self.s.pk]), ("core:export", ["customers"]), ("core:search", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("billing:action", args=[self.inv.pk, "pay"]), {"amount": "1"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 302)  # anonymous -> login

    def test_exports_respect_capabilities(self):
        self.assertEqual(self.status("sales", "get", reverse("core:export", args=["payments"])), 404)
        self.assertEqual(self.status("finance", "get", reverse("core:export", args=["payments"])), 200)
        self.assertEqual(self.status("sales", "get", reverse("core:export", args=["customers"])), 200)


class PortalIsolation(Base):
    def setUp(self):
        super().setUp()
        self.me_user = User.objects.create_user("me@x.test", PW, role="customer")
        self.cust.user = self.me_user
        self.cust.save()
        self.other = Customer.objects.create(name="Other", email="other@x.test")
        self.mine = make_active(self.plan, self.cust, self.today)
        self.theirs = make_active(self.plan, self.other, self.today)
        billing.run_billing(self.today)
        self.my_inv = Invoice.objects.get(subscription=self.mine)
        self.their_inv = Invoice.objects.get(subscription=self.theirs)
        for i in (self.my_inv, self.their_inv):
            billing.confirm_invoice(i)
        self.client.force_login(self.me_user)

    def test_every_portal_url_404s_for_other_customers_records(self):
        t, ti = self.theirs.pk, self.their_inv.pk
        gets = [("core:portal_subscription", [t]), ("core:portal_change", [t]), ("core:portal_invoice", [ti]), ("core:portal_pay", [ti]),
                ("core:portal_invoice_pdf", [ti]), ("core:portal_invoice_print", [ti])]
        for name, args in gets:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404, name)
        for action in ("close", "pause", "addon_on", "accept", "renew", "keep"):
            self.assertEqual(self.client.post(reverse("core:portal_action", args=[t, action])).status_code, 404, action)
        self.assertEqual(self.client.post(reverse("core:portal_pay", args=[ti]), {"amount": "1", "method": "upi", "reference": "x", "date": "2026-01-01"}).status_code, 404)

    def test_own_records_open(self):
        for name, args in (("core:portal", []), ("core:portal_subscription", [self.mine.pk]), ("core:portal_invoice", [self.my_inv.pk]), ("core:portal_invoice_pdf", [self.my_inv.pk]), ("core:portal_pay", [self.my_inv.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("core:portal_invoice_pdf", args=[self.my_inv.pk]))["Content-Type"], "application/pdf")

    def test_drafts_and_cancelled_invoices_are_invisible_to_customers(self):
        draft = Invoice.objects.create(subscription=self.mine, customer=self.cust, period_start=self.today, period_end=self.today, due_date=self.today, bill_to_name="x", bill_to_email="x@x.test", kind="adjustment")
        self.assertEqual(self.client.get(reverse("core:portal_invoice", args=[draft.pk])).status_code, 404)

    def test_profile_form_ignores_email_tampering_and_never_touches_past_invoices(self):
        self.client.post(reverse("core:portal_profile"), {"p-name": "Me", "p-company": "NewCo", "p-phone": "", "p-address": "Elsewhere", "p-tax_id": "", "p-email": "hijack@evil.test"})
        self.cust.refresh_from_db()
        self.assertEqual((self.cust.address, self.cust.email.startswith("hijack")), ("Elsewhere", False))
        self.my_inv.refresh_from_db()
        self.assertEqual(self.my_inv.bill_to_address, "")

    def test_acceptance_needs_name_and_consent_and_records_it(self):
        s = subs.create_subscription(customer=self.cust, plan=self.plan)
        subs.send_quotation(s)
        url = reverse("core:portal_action", args=[s.pk, "accept"])
        self.client.post(url, {"signed_name": "", "agree": "on"})
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "quotation")
        self.client.post(url, {"signed_name": "Me Myself", "agree": "on"})
        s.refresh_from_db()
        self.assertEqual(s.state, "confirmed")
        a = s.acceptances.get()
        self.assertEqual((a.signed_name, a.accepted_by_id), ("Me Myself", self.me_user.pk))
        self.assertIsNotNone(a.ip)

    def test_self_service_respects_plan_flags(self):
        locked, _ = make_world(self_serve_changes=False, self_serve_cancel=False)
        s = make_active(locked, self.cust, self.today)
        self.assertEqual(self.client.get(reverse("core:portal_change", args=[s.pk])).status_code, 302)
        self.client.post(reverse("core:portal_action", args=[s.pk, "close"]), {"reason": "other"})
        self.assertEqual(Subscription.objects.get(pk=s.pk).state, "active")

    def test_customer_cancel_flow_records_reason(self):
        billing.run_billing(self.today)
        self.client.post(reverse("core:portal_action", args=[self.mine.pk, "close"]), {"reason": "too_expensive", "note": "budget"})
        s = Subscription.objects.get(pk=self.mine.pk)
        self.assertEqual((s.close_reason, s.close_note), ("too_expensive", "budget"))
        self.assertIsNotNone(s.pending_cancel_at)

    def test_customer_reports_payment_staff_confirms(self):
        r = self.client.post(reverse("core:portal_pay", args=[self.my_inv.pk]), {"amount": str(self.my_inv.total), "method": "upi", "reference": "UTR55", "date": self.today.isoformat()})
        self.assertEqual(r.status_code, 302)
        self.my_inv.refresh_from_db()
        self.assertEqual(self.my_inv.amount_paid, 0)
        self.assertEqual(self.my_inv.payments.get().status, "pending")


class Hardening(Base):
    def test_csv_exports_neutralise_formulas(self):
        from core import csvsafe
        self.assertEqual(csvsafe.safe("=HYPERLINK(\"http://evil\")"), "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(csvsafe.safe("@cmd"), "'@cmd")
        self.assertEqual(csvsafe.safe("Plain"), "Plain")
        self.assertEqual(csvsafe.safe(12), 12)
        Customer.objects.create(name="=1+1", email="f@x.test")
        self.client.force_login(self.u["admin"])
        body = self.client.get(reverse("core:export", args=["customers"])).content.decode()
        self.assertIn("'=1+1", body)
        self.assertNotIn("\n=1+1", body)

    def test_post_redirect_targets_cannot_leave_the_site(self):
        s = make_active(self.plan, self.cust, self.today)
        billing.run_billing(self.today)
        inv = Invoice.objects.get()
        billing.confirm_invoice(inv)
        self.client.force_login(self.u["admin"])
        for evil in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)"):
            r = self.client.post(reverse("billing:quick_pay"), {"invoice": inv.pk, "amount": "1", "method": "bank", "date": self.today.isoformat(), "next": evil})
            self.assertTrue(r.url.startswith("/") and not r.url.startswith("//"), (evil, r.url))
        r = self.client.post(reverse("billing:quick_pay"), {"invoice": inv.pk, "amount": "1", "method": "bank", "date": self.today.isoformat(), "next": "/billing/payments/"})
        self.assertEqual(r.url, "/billing/payments/")

    def test_security_headers(self):
        self.client.force_login(self.u["admin"])
        r = self.client.get(reverse("core:dashboard"))
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_unauthenticated_post_to_state_changing_urls_redirects_to_login(self):
        for url in (reverse("billing:runs"), reverse("billing:bulk"), reverse("subscriptions:action", args=[1, "activate"])):
            r = self.client.post(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/accounts/login/", r.url)

    def test_webhook_rejects_get_and_unsigned_post(self):
        self.assertEqual(self.client.get(reverse("billing:razorpay_webhook")).status_code, 405)
        self.assertEqual(self.client.post(reverse("billing:razorpay_webhook"), data="{}", content_type="application/json").status_code, 400)
