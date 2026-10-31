from datetime import timedelta

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from billing import services as billing
from billing.models import Invoice
from core.views_reports import _report_page

from .helpers import Base, make_active, make_world


class ReportPrintPagination(SimpleTestCase):
    def test_print_includes_first_thousand_rows_and_ignores_ui_page(self):
        request = RequestFactory().get("/reports/", {"print": "1", "page": "3"})
        page = _report_page(request, list(range(1007)))
        self.assertEqual((page.number, page.end_index(), page.paginator.count), (1, 1000, 1007))


class ReportPrintPresentation(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.u["admin"])

    def test_all_reports_include_document_identity_and_defined_scope(self):
        for name in ("reports", "report_mrr", "report_retention", "report_receivables", "report_breakdown"):
            with self.subTest(report=name):
                response = self.client.get(reverse("core:" + name), {"print": "1"})
                self.assertContains(response, "data-report-print")
                self.assertContains(response, "Brightloop Studio")
                self.assertContains(response, "All customers, plans and products")
                self.assertContains(response, "Currency <b>INR</b>")
                self.assertContains(response, "Prepared ")

    def test_filter_labels_are_escaped_and_return_link_keeps_scope(self):
        self.plan.name = '<img src=x onerror="alert(1)">'
        self.plan.save(update_fields=["name"])
        response = self.client.get(reverse("core:reports"), {"print": "1", "plan": self.plan.pk, "view": "paid",
                                                           "from": "2026-10-31", "to": "2026-01-01"})
        self.assertContains(response, "&lt;img src=x")
        self.assertNotContains(response, '<img src=x')
        self.assertEqual(response.context["start"].isoformat(), "2026-01-01")
        self.assertEqual(response.context["end"].isoformat(), "2026-10-31")
        back = response.context["report_return_url"]
        self.assertIn("plan=", back)
        self.assertIn("view=paid", back)
        self.assertNotIn("print=", back)

    def test_print_has_more_than_ui_page_and_reports_exact_row_count(self):
        plan, customer = make_world(interval="daily")
        sub = make_active(plan, customer, self.today - timedelta(days=40))
        billing.run_billing(self.today, only=[sub.pk])
        for invoice in Invoice.objects.filter(subscription=sub):
            billing.confirm_invoice(invoice)
        url = reverse("core:reports")
        regular = self.client.get(url, {"view": "outstanding"})
        printed = self.client.get(url, {"view": "outstanding", "print": "1", "page": "2"})
        count = Invoice.objects.filter(subscription=sub).count()
        self.assertGreater(count, 25)
        self.assertEqual(len(regular.context["page"]), 25)
        self.assertEqual(len(printed.context["page"]), count)
        self.assertContains(printed, f"of {count} matching invoices")

    def test_print_flag_does_not_grant_finance_access(self):
        self.client.force_login(self.u["sales"])
        for name in ("reports", "report_mrr", "report_retention", "report_receivables", "report_breakdown"):
            self.assertEqual(self.client.get(reverse("core:" + name), {"print": "1"}).status_code, 403)

    def test_missing_record_filter_is_explicit_instead_of_claiming_all_records(self):
        response = self.client.get(reverse("core:reports"), {"print": "1", "customer": "999999"})
        self.assertContains(response, "Unavailable record #999999")
        self.assertNotContains(response, "All customers, plans and products")
