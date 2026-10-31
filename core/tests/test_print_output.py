import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from unittest import skipUnless

from django.urls import reverse

from billing import services as billing
from billing.models import Invoice, InvoiceLine
from core.models import CompanySettings

from .helpers import Base, make_active

PDF_TOOLS_AVAILABLE = bool(shutil.which("pdfinfo") and shutil.which("pdftotext"))


def pdf_info(pdf):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as stream:
        stream.write(pdf)
        stream.flush()
        result = subprocess.run(["pdfinfo", stream.name], check=True, capture_output=True, text=True)
    return int(re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE).group(1))


def pdf_text(pdf):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as stream:
        stream.write(pdf)
        stream.flush()
        result = subprocess.run(["pdftotext", "-layout", stream.name, "-"], check=True, capture_output=True, text=True)
    return result.stdout


class InvoicePrintOutput(Base):
    def setUp(self):
        super().setUp()
        sub = make_active(self.plan, self.cust, self.today, qty=2, user=self.u["admin"])
        billing.run_billing(self.today, only=[sub.pk])
        self.inv = Invoice.objects.get(subscription=sub)
        self.client.force_login(self.u["admin"])

    def print_html(self):
        return self.client.get(reverse("billing:print", args=[self.inv.pk]))

    def test_html_payment_activity_labels_refunds_and_avoids_false_receipt(self):
        billing.confirm_invoice(self.inv)
        billing.record_payment(self.inv, amount=Decimal("100.00"), method="upi")
        billing.refund_payment(self.inv, amount=Decimal("25.00"), method="upi")
        billing.record_payment(self.inv, amount=Decimal("10.00"), method="bank")
        html = self.print_html()
        self.assertContains(html, "Payment activity")
        self.assertContains(html, "Refund")
        self.assertNotContains(html, "Received with thanks")

    @skipUnless(PDF_TOOLS_AVAILABLE, "Install Poppler pdfinfo and pdftotext to verify PDF output")
    def test_short_invoice_pdf_fits_one_page_and_labels_refunds(self):
        billing.confirm_invoice(self.inv)
        billing.record_payment(self.inv, amount=Decimal("100.00"), method="upi")
        billing.refund_payment(self.inv, amount=Decimal("25.00"), method="upi")
        billing.record_payment(self.inv, amount=Decimal("10.00"), method="bank")
        from billing.pdf import render_invoice_pdf
        pdf = render_invoice_pdf(self.inv)
        self.assertEqual(pdf_info(pdf), 1)
        text = pdf_text(pdf)
        self.assertIn("Payment", text)
        self.assertIn("Refund", text)
        self.assertNotIn("Received with thanks", text)

    def test_fully_paid_invoice_can_show_receipt_acknowledgement(self):
        billing.confirm_invoice(self.inv)
        billing.record_payment(self.inv, amount=self.inv.total, method="bank")

        html = self.print_html()
        self.assertContains(html, "Received with thanks")

    @skipUnless(PDF_TOOLS_AVAILABLE, "Install Poppler pdfinfo and pdftotext to verify PDF output")
    def test_fully_paid_pdf_can_show_receipt_acknowledgement(self):
        billing.confirm_invoice(self.inv)
        billing.record_payment(self.inv, amount=self.inv.total, method="bank")
        from billing.pdf import render_invoice_pdf
        self.assertIn("Received with thanks", pdf_text(render_invoice_pdf(self.inv)))

    def test_unpaid_invoice_does_not_claim_receipt(self):
        html = self.print_html()
        self.assertNotContains(html, "Received with thanks")

    @skipUnless(PDF_TOOLS_AVAILABLE, "Install Poppler pdfinfo and pdftotext to verify PDF output")
    def test_unpaid_pdf_does_not_claim_receipt(self):
        from billing.pdf import render_invoice_pdf
        self.assertNotIn("Received with thanks", pdf_text(render_invoice_pdf(self.inv)))

    @skipUnless(PDF_TOOLS_AVAILABLE, "Install Poppler pdfinfo and pdftotext to verify PDF output")
    def test_long_invoice_spans_pages_and_repeats_table_heading(self):
        from billing.pdf import render_invoice_pdf

        count = 70
        InvoiceLine.objects.bulk_create([
            InvoiceLine(invoice=self.inv, description=f"Print regression item {i:02d}", quantity=1,
                        unit_price=Decimal("100.00"), tax_rate=Decimal("18.00"), tax_name="GST",
                        tax_amount=Decimal("18.00"), amount=Decimal("100.00"))
            for i in range(count)
        ])
        self.inv.subtotal += Decimal(count * 100)
        self.inv.tax_total += Decimal(count * 18)
        self.inv.total = self.inv.subtotal - self.inv.discount_total + self.inv.tax_total
        self.inv.save(update_fields=["subtotal", "tax_total", "total"])
        billing.confirm_invoice(self.inv)

        pdf = render_invoice_pdf(self.inv)
        pages = pdf_info(pdf)
        text = pdf_text(pdf)
        self.assertGreater(pages, 1)
        item_pages = [page for page in text.split("\f") if "Print regression item" in page]
        self.assertGreaterEqual(len(item_pages), 2)
        self.assertTrue(all("Description" in page for page in item_pages))
        self.assertIn("Print regression item 69", text)

    @skipUnless(PDF_TOOLS_AVAILABLE, "Install Poppler pdfinfo and pdftotext to verify PDF output")
    def test_reportlab_escapes_untrusted_model_text(self):
        from billing.pdf import render_invoice_pdf

        payload = "<img src='file:///etc/passwd' width='8' height='8'/><b>literal</b>"
        company = CompanySettings.get()
        company.name = f"Studio {payload}"
        company.address = f"{payload}\nSuite 4"
        company.invoice_terms = f"{payload}\nTerms continue."
        company.bank_details = payload
        company.invoice_footer = payload
        company.save()
        self.inv.bill_to_name = f"Customer {payload}"
        self.inv.bill_to_address = payload
        self.inv.bill_to_email = "customer@example.test"
        self.inv.save(update_fields=["bill_to_name", "bill_to_address", "bill_to_email"])
        line = self.inv.lines.get()
        line.description = payload
        line.save(update_fields=["description"])

        text = pdf_text(render_invoice_pdf(self.inv))
        self.assertGreaterEqual(text.count("<img src='file:///etc/passwd'"), 4)
        self.assertIn("<b>literal</b>", text)
