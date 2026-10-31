from datetime import timedelta
from decimal import Decimal

from django.urls import reverse

from accounts.models import Customer, User
from billing import services as billing
from billing.models import Invoice
from core.numwords import rupees_in_words as words
from subscriptions import services as subs
from subscriptions.models import Subscription

from .helpers import PW, Base, make_active


class Words(Base):
    def test_indian_numbering(self):
        self.assertEqual(words(0), "Rupees Zero Only")
        self.assertEqual(words(100000), "Rupees One Lakh Only")
        self.assertEqual(words(25000000), "Rupees Two Crore Fifty Lakh Only")
        self.assertEqual(words(Decimal("7646.40")), "Rupees Seven Thousand Six Hundred Forty Six and Forty Paise Only")
        self.assertEqual(words(Decimal("0.05")), "Rupees Zero and Five Paise Only")
        self.assertEqual(words(-180), "Minus Rupees One Hundred Eighty Only")
        self.assertEqual(words(Decimal("1234567.89")), "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Eighty Nine Paise Only")


class Documents(Base):
    def setUp(self):
        super().setUp()
        self.s = make_active(self.plan, self.cust, self.today - timedelta(days=3), qty=2, user=self.u["admin"])
        billing.run_billing(self.today)
        self.inv = Invoice.objects.get()
        billing.confirm_invoice(self.inv)
        self.client.force_login(self.u["admin"])

    def test_invoice_document_has_the_legal_elements(self):
        r = self.client.get(reverse("billing:print", args=[self.inv.pk]))
        for needle in ("Tax invoice", self.inv.number, "Bill to", "Taxable value", "Tax summary", "Amount in words", "Rupees Two Thousand Three Hundred Sixty Only",
                       "Authorised signatory", "Terms and conditions", self.inv.fingerprint, "Page"):
            self.assertContains(r, needle, msg_prefix=needle)
        self.assertNotContains(r, "class=\"stamp paid\"")
        billing.record_payment(self.inv, amount=self.inv.total, method="bank")
        self.assertContains(self.client.get(reverse("billing:print", args=[self.inv.pk])), "stamp paid")

    def test_embed_mode_hides_the_page_toolbar_via_class(self):
        self.assertContains(self.client.get(reverse("billing:print", args=[self.inv.pk]), {"embed": 1}), '<body class="embed">')
        self.assertContains(self.client.get(reverse("billing:print", args=[self.inv.pk])), '<body class="">')

    def test_fingerprint_changes_if_content_would_change(self):
        a = self.inv.fingerprint
        Invoice.objects.filter(pk=self.inv.pk).update(bill_to_name="Someone else")
        self.assertNotEqual(Invoice.objects.get(pk=self.inv.pk).fingerprint, a)

    def test_draft_and_cancelled_are_watermarked(self):
        s2 = make_active(*__import__("core.tests.helpers", fromlist=["make_world"]).make_world(), start=self.today)
        billing.run_billing(self.today)
        draft = Invoice.objects.filter(state="draft").first()
        self.assertContains(self.client.get(reverse("billing:print", args=[draft.pk])), "DRAFT")

    def test_quotation_agreement_and_acceptance_record(self):
        q = subs.create_subscription(customer=self.cust, plan=self.plan, user=self.u["admin"])
        r = self.client.get(reverse("subscriptions:print", args=[q.pk]))
        self.assertContains(r, "Quotation"); self.assertContains(r, "DRAFT"); self.assertContains(r, "Accepted for")
        subs.send_quotation(q)
        subs.accept(Subscription.objects.get(pk=q.pk), actor=self.u["admin"], signed_name="Alex Doe", ip="203.0.113.7")
        r = self.client.get(reverse("subscriptions:print", args=[q.pk]))
        self.assertContains(r, "Accepted electronically"); self.assertContains(r, "Alex Doe"); self.assertContains(r, "203.0.113.7")
        self.assertContains(r, "Order confirmation")

    def test_statement_totals_and_ageing(self):
        billing.record_payment(self.inv, amount=1000, method="bank")
        r = self.client.get(reverse("core:customer_statement", args=[self.cust.pk]))
        self.assertContains(r, "Statement of account"); self.assertContains(r, "₹1,360.00"); self.assertContains(r, "Ageing")
        self.assertContains(r, "Rupees One Thousand Three Hundred Sixty Only")

    def test_pdf_embeds_a_font_with_the_rupee_sign(self):
        from billing.pdf import render_invoice_pdf
        pdf = render_invoice_pdf(self.inv)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn(b"NotoSans", pdf)  # built-in Helvetica has no rupee glyph and prints a black box

    def test_viewer_markup_and_links(self):
        r = self.client.get(reverse("billing:detail", args=[self.inv.pk]))
        self.assertContains(r, 'id="docview"')
        self.assertContains(r, "data-doc ")
        self.assertNotContains(r, 'print/" target="_blank"')


class PortalDocuments(Base):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("me@x.test", PW, role="customer")
        self.cust.user = self.user
        self.cust.save()
        self.other = Customer.objects.create(name="Other", email="o@x.test")
        self.mine = make_active(self.plan, self.cust, self.today)
        self.theirs = make_active(self.plan, self.other, self.today)
        billing.run_billing(self.today)
        for i in Invoice.objects.all():
            billing.confirm_invoice(i)
        self.client.force_login(self.user)

    def test_own_documents_open_and_others_do_not(self):
        mine, theirs = Invoice.objects.get(subscription=self.mine), Invoice.objects.get(subscription=self.theirs)
        self.assertEqual(self.client.get(reverse("core:portal_invoice_print", args=[mine.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:portal_invoice_print", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("core:portal_subscription_print", args=[self.mine.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:portal_subscription_print", args=[self.theirs.pk])).status_code, 404)
        r = self.client.get(reverse("core:portal_statement"))
        self.assertContains(r, mine.number)
        self.assertNotContains(r, theirs.number)
        self.assertEqual(self.client.get(reverse("billing:print", args=[mine.pk])).status_code, 403)  # staff-only route
