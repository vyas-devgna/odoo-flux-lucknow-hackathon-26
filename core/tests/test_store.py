from django.urls import reverse

from accounts.models import Customer, User
from billing.models import Invoice
from catalog.models import Plan, Product
from subscriptions.models import Subscription

from .helpers import PW, Base, make_world


class Storefront(Base):
    def setUp(self):
        super().setUp()
        self.plan.published = True
        self.plan.activation = "acceptance"
        self.plan.save()
        self.addon = Product.objects.create(name="Pack", type="addon", sales_price=300, published=True)
        self.plan.products.add(self.addon)
        self.user = User.objects.create_user("buyer@x.test", PW, role="customer", name="Buyer")
        Customer.objects.create(name="Buyer", email="buyer@x.test", user=self.user)

    def test_public_pages_show_only_published_plans(self):
        hidden, _ = make_world(price=5)
        hidden.name = "Hidden"
        hidden.save()
        r = self.client.get(reverse("store:home"))
        self.assertContains(r, self.plan.name)
        self.assertNotContains(r, "Hidden")
        self.assertEqual(self.client.get(reverse("store:plan", args=[hidden.pk])).status_code, 404)

    def test_live_summary_prices_server_side(self):
        r = self.client.get(reverse("store:plan", args=[self.plan.pk]), {"quantity": 3, "addon": self.addon.pk}, HTTP_HX_TARGET="summary")
        self.assertContains(r, "₹3,300.00")  # 3 x 1000 + 300
        self.assertContains(r, "₹3,894.00")  # incl. 18% tax

    def test_checkout_requires_login_and_acceptance_consent(self):
        url = reverse("store:checkout", args=[self.plan.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        self.assertEqual(Subscription.objects.count(), 0)
        self.client.force_login(self.user)
        self.client.post(url, {"quantity": 1})
        self.assertEqual(Subscription.objects.count(), 0)  # no name / consent, no order

    def test_checkout_creates_accepted_active_contract_and_first_invoice(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("store:checkout", args=[self.plan.pk]), {"quantity": 2, "addon": self.addon.pk, "signed_name": "Buyer B", "agree": "on"})
        s = Subscription.objects.get()
        self.assertEqual((s.state, s.lines.count(), s.acceptances.get().signed_name), ("active", 2, "Buyer B"))
        inv = Invoice.objects.get()
        self.assertEqual(inv.subtotal, 2300)
        self.assertEqual(r.url, reverse("core:portal_pay", args=[inv.pk]))

    def test_pay_to_activate_waits_for_payment(self):
        self.plan.activation = "payment"
        self.plan.save()
        self.client.force_login(self.user)
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), {"quantity": 1, "signed_name": "B", "agree": "on"})
        s = Subscription.objects.get()
        self.assertEqual(s.state, "confirmed")
        self.assertEqual(Invoice.objects.get().state, "confirmed")

    def test_staff_cannot_check_out(self):
        self.client.force_login(self.u["admin"])
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), {"quantity": 1, "signed_name": "A", "agree": "on"})
        self.assertEqual(Subscription.objects.count(), 0)
