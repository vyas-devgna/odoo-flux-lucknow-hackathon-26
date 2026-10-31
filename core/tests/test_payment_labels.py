from django.urls import reverse

from accounts.models import User
from billing import services
from billing.models import Invoice

from .helpers import Base, PW, make_active


class PaymentLabels(Base):
    def test_refunds_are_labelled_in_staff_and_customer_views(self):
        sub = make_active(self.plan, self.cust, self.today)
        services.run_billing(self.today, only=[sub.pk])
        invoice = Invoice.objects.get(subscription=sub)
        services.confirm_invoice(invoice)
        services.record_payment(invoice, amount=invoice.total, method="bank")
        services.refund_payment(invoice, amount=10, method="bank")
        self.client.force_login(self.u["admin"])
        for name, args in (("billing:detail", [invoice.pk]), ("billing:payments", [])):
            self.assertContains(self.client.get(reverse(name, args=args)), "Refunded")
        customer = User.objects.create_user("customer@labels.test", PW, role="customer")
        self.cust.user = customer
        self.cust.save(update_fields=["user"])
        self.client.force_login(customer)
        self.assertContains(self.client.get(reverse("core:portal_invoice", args=[invoice.pk])), "Refunded")
