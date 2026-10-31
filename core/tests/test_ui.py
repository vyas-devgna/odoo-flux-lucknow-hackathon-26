from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Customer, User
from billing import services as billing
from billing.models import Invoice
from catalog.models import Plan, Product
from core.models import AuditLog, Task
from subscriptions import services as subs
from subscriptions.models import Subscription

from .helpers import PW, Base, make_active, make_world


class Smoke(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def urls(self):
        sub = Subscription.objects.filter(state="active").first()
        draft = Subscription.objects.filter(state="draft").first()
        inv = Invoice.objects.filter(kind="recurring").first()
        cn = Invoice.objects.filter(kind="credit_note").first()
        cust = Customer.objects.first()
        u = [reverse(n) for n in ("core:dashboard", "core:reports", "core:report_mrr", "core:report_retention", "core:report_receivables", "core:report_breakdown", "core:customers",
                                  "core:customer_new", "core:customer_import", "subscriptions:list", "subscriptions:quotes", "subscriptions:pipeline", "subscriptions:renewals",
                                  "subscriptions:changes", "subscriptions:new", "billing:list", "billing:credit_notes", "billing:payments", "billing:runs", "accounts:team", "accounts:team_new",
                                  "core:settings", "core:settings_payments", "core:automation", "core:automation_rule", "core:outbox", "core:audit", "store:home", "core:search")]
        u += [reverse("subscriptions:detail", args=[sub.pk]), reverse("subscriptions:edit", args=[draft.pk]), reverse("subscriptions:change", args=[sub.pk]), reverse("subscriptions:print", args=[sub.pk]),
              reverse("billing:detail", args=[inv.pk]), reverse("billing:detail", args=[cn.pk]), reverse("billing:print", args=[inv.pk]), reverse("billing:pdf", args=[inv.pk]),
              reverse("core:customer_edit", args=[cust.pk]), reverse("core:customer_statement", args=[cust.pk]), reverse("store:plan", args=[Plan.objects.filter(published=True).first().pk])]
        for r in ("products", "plans", "taxes", "categories", "templates", "discounts"):
            u += [reverse(f"catalog:{r}_list"), reverse(f"catalog:{r}_new")]
        u += [reverse("catalog:products_edit", args=[Product.objects.first().pk]), reverse("catalog:plans_edit", args=[Plan.objects.first().pk])]
        return u

    def test_every_staff_page_renders_for_admin(self):
        self.client.force_login(User.objects.get(email="admin@brightloop.test"))
        for u in self.urls():
            r = self.client.get(u)
            self.assertEqual(r.status_code, 200, u)

    def test_filters_and_htmx_partials_render(self):
        self.client.force_login(User.objects.get(email="admin@brightloop.test"))
        sub = Subscription.objects.filter(state="active").first()
        for u in (reverse("billing:list") + "?status=overdue", reverse("billing:list") + "?sort=due&q=INV", reverse("subscriptions:list") + "?state=active&sort=-customer",
                  reverse("subscriptions:list") + "?flag=exception", reverse("billing:payments") + "?status=pending", reverse("core:reports") + "?plan=1&view=paid",
                  reverse("core:report_breakdown") + "?by=product&from=2026-01-01&to=2026-12-31", reverse("core:dashboard") + "?range=year", reverse("core:search") + "?q=acme",
                  reverse("subscriptions:changes") + "?movement=expansion"):
            self.assertEqual(self.client.get(u).status_code, 200, u)
        r = self.client.get(reverse("subscriptions:change", args=[sub.pk]), {"quantity": 9, "touched": 1}, HTTP_HX_TARGET="preview")
        self.assertContains(r, "Cost preview")
        self.assertNotContains(r, "<html")
        self.assertEqual(self.client.get(reverse("core:search"), {"q": "acme", "fmt": "json"}).json()["results"][0]["k"], "Customers")
        for what in ("subscriptions", "payments", "customers"):
            self.assertEqual(self.client.get(reverse("core:export", args=[what]))["Content-Type"], "text/csv")
        self.assertEqual(self.client.get(reverse("billing:list") + "?export=csv")["Content-Type"], "text/csv")
        self.assertEqual(self.client.get(reverse("billing:pdf", args=[Invoice.objects.first().pk]))["Content-Type"], "application/pdf")

    def test_each_staff_capability_sees_a_working_app(self):
        for email in ("staff@brightloop.test", "finance@brightloop.test", "catalog@brightloop.test"):
            self.client.force_login(User.objects.get(email=email))
            r = self.client.get(reverse("core:dashboard"))
            self.assertEqual(r.status_code, 200, email)

    def test_customer_portal_pages(self):
        self.client.force_login(User.objects.get(email="maya@acme.test"))
        c = User.objects.get(email="maya@acme.test").customer
        sub = c.subscriptions.filter(state="active").first()
        inv = c.invoices.filter(state="confirmed").first()
        urls = [reverse("core:portal"), reverse("core:portal_subscriptions"), reverse("core:portal_subscription", args=[sub.pk]), reverse("core:portal_change", args=[sub.pk]),
                reverse("core:portal_invoices"), reverse("core:portal_invoices") + "?status=due", reverse("core:portal_invoice", args=[inv.pk]), reverse("core:portal_pay", args=[inv.pk]),
                reverse("core:portal_invoice_pdf", args=[inv.pk]), reverse("core:portal_profile"), reverse("store:home")]
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 200, u)

    def test_seed_is_idempotent_and_reconciles(self):
        from core import reports
        from django.utils import timezone
        n = Subscription.objects.count()
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Subscription.objects.count(), n)
        self.assertGreaterEqual(n, 40)
        self.assertGreaterEqual(Invoice.objects.exclude(kind="credit_note").count(), 40)
        self.assertGreaterEqual(Customer.objects.count(), 20)
        live = sum(s.recurring_monthly for s in Subscription.objects.filter(state="active", start_date__lte=timezone.localdate()))
        self.assertEqual(reports.mrr_at(timezone.localdate()), live)  # movement history reconciles with live contracts
        for inv in Invoice.objects.exclude(state="cancelled").exclude(kind="credit_note"):
            self.assertEqual(inv.total, sum(l.amount + l.tax_amount for l in inv.lines.all()), inv.number)
            self.assertEqual(inv.total, inv.subtotal - inv.discount_total + inv.tax_total, inv.number)
            self.assertGreaterEqual(inv.balance, 0 if inv.state != "paid" else -1000000)

    def test_page_html_uses_brightloop_branding(self):
        self.client.force_login(User.objects.get(email="admin@brightloop.test"))
        for u in (reverse("core:dashboard"), reverse("store:home"), reverse("billing:list")):
            self.assertContains(self.client.get(u), "Brightloop Studio")


class QueryBudgets(Base):
    """Pages must not issue per-row queries: the count with 3 rows and with 30 rows is the same."""

    def seed(self, n):
        for i in range(n):
            c = Customer.objects.create(name=f"C{i}", email=f"qc{Customer.objects.count()}@x.test", company=f"Co{i}")
            make_active(self.plan, c, self.today - timedelta(days=40))
        billing.run_billing(self.today)

    def count(self, url):
        self.client.force_login(self.u["admin"])
        self.client.get(url)  # warm caches
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_lists_do_not_n_plus_one(self):
        urls = [reverse("subscriptions:list"), reverse("billing:list"), reverse("billing:payments"), reverse("core:customers"), reverse("subscriptions:quotes"),
                reverse("subscriptions:renewals"), reverse("subscriptions:changes"), reverse("core:dashboard"), reverse("core:report_receivables")]
        self.seed(3)
        small = {u: self.count(u) for u in urls}
        self.seed(27)
        for u in urls:
            self.assertLessEqual(self.count(u), small[u] + 2, u)
        self.assertLess(small[reverse("core:dashboard")], 70)


class Imports(Base):
    def test_customer_csv_import_creates_updates_and_reports_rejections(self):
        self.client.force_login(self.u["admin"])
        csv_data = "name,email,company\nAsha,asha@v.test,V Foods\nBad,not-an-email,\nUpdated,ALEX0@X.TEST,New Co\n,noname@x.test,\n"
        r = self.client.post(reverse("core:customer_import"), {"file": SimpleUploadedFile("c.csv", csv_data.encode())})
        self.assertContains(r, "Rejected")
        self.assertTrue(Customer.objects.filter(email="asha@v.test").exists())
        self.assertEqual(Customer.objects.filter(email__iexact="alex0@x.test").count(), 1)
        self.assertEqual(Customer.objects.get(email__iexact="alex0@x.test").company, "New Co")
        r = self.client.post(reverse("core:customer_import"), {"file": SimpleUploadedFile("c.csv", b"foo,bar\n1,2\n")}, follow=True)
        self.assertContains(r, "headers")

    def test_duplicate_customer_email_is_rejected_in_the_form(self):
        self.client.force_login(self.u["admin"])
        r = self.client.post(reverse("core:customer_new"), {"name": "Dup", "email": self.cust.email.upper(), "company": "", "phone": "", "address": "", "tax_id": ""})
        self.assertEqual((r.status_code, Customer.objects.count()), (200, 1))


class Workflow(Base):
    def test_draft_period_invoice_is_regenerated_on_amendment_not_prorated(self):
        s = make_active(self.plan, self.cust, self.today, qty=2)
        billing.run_billing(self.today)
        draft = Invoice.objects.get()
        res = subs.apply_change(Subscription.objects.get(pk=s.pk), quantity=5, effective="now")
        draft.refresh_from_db()
        self.assertEqual(draft.state, "cancelled")
        new = Invoice.objects.get(replaces=draft)
        self.assertEqual((res["document"].pk, new.subtotal), (new.pk, 5000))

    def test_audit_trail_covers_a_whole_journey(self):
        self.client.force_login(self.u["admin"])
        s = subs.create_subscription(customer=self.cust, plan=self.plan, user=self.u["admin"])
        self.client.post(reverse("subscriptions:action", args=[s.pk, "send"]))
        self.client.post(reverse("subscriptions:action", args=[s.pk, "confirm"]))
        self.client.post(reverse("subscriptions:action", args=[s.pk, "activate"]))
        self.client.post(reverse("billing:runs"))
        inv = Invoice.objects.get()
        self.client.post(reverse("billing:action", args=[inv.pk, "confirm"]))
        self.client.post(reverse("billing:action", args=[inv.pk, "pay"]), {"amount": "100", "method": "upi", "date": self.today.isoformat()})
        actions = set(AuditLog.objects.values_list("action", flat=True))
        for a in ("subscription.send", "subscription.confirm", "subscription.activate", "billing.run", "invoice.confirm", "payment.record"):
            self.assertIn(a, actions)
