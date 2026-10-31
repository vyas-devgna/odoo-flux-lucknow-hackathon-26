from django.urls import reverse
from unittest.mock import patch
from django.http import QueryDict

from accounts.models import Customer, User
from billing.models import Invoice
from catalog.models import Plan, Product, ProductVariant
from subscriptions.models import Subscription
from store import services as storefront
from store.models import CheckoutReceipt

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

    def order(self, **options):
        return {"quantity": 1, "signed_name": "Buyer B", "agree": "on",
                "checkout_token": storefront.checkout_token(self.user, self.plan), **options}

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

    def test_default_variant_in_preview_matches_the_selected_dropdown_and_order(self):
        product = Product.objects.create(name="Suite", sales_price=100, published=True)
        variant = ProductVariant.objects.create(product=product, value="Premium", extra_price=560)
        self.plan.products.add(product)
        response = self.client.get(reverse("store:plan", args=[self.plan.pk]), {f"bundle_{product.pk}": "1"})
        self.assertEqual(response.context["variants"][product.pk], variant.pk)
        self.assertEqual(response.context["net"], 1660)
        self.client.force_login(self.user)
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order(**{f"bundle_{product.pk}": "1"}))
        self.assertEqual(Subscription.objects.get().lines.get(product=product).unit_price, 660)

    def test_checkout_requires_login_and_acceptance_consent(self):
        url = reverse("store:checkout", args=[self.plan.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        self.assertEqual(Subscription.objects.count(), 0)
        self.client.force_login(self.user)
        self.client.post(url, {"quantity": 1})
        self.assertEqual(Subscription.objects.count(), 0)  # no name / consent, no order

    def test_checkout_creates_accepted_active_contract_and_first_invoice(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order(quantity=2, addon=self.addon.pk))
        s = Subscription.objects.get()
        self.assertEqual((s.state, s.lines.count(), s.acceptances.get().signed_name), ("active", 2, "Buyer B"))
        inv = Invoice.objects.get()
        self.assertEqual(inv.subtotal, 2300)
        self.assertEqual(r.url, reverse("core:portal_pay", args=[inv.pk]))

    def test_pay_to_activate_waits_for_payment(self):
        self.plan.activation = "payment"
        self.plan.save()
        self.client.force_login(self.user)
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order())
        s = Subscription.objects.get()
        self.assertEqual(s.state, "confirmed")
        self.assertEqual(Invoice.objects.get().state, "confirmed")

    def test_staff_cannot_check_out(self):
        self.client.force_login(self.u["admin"])
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), {"quantity": 1, "signed_name": "A", "agree": "on"})
        self.assertEqual(Subscription.objects.count(), 0)

    def test_repeated_checkout_reuses_contract_invoice_and_consent(self):
        self.client.force_login(self.user)
        payload = self.order()
        url = reverse("store:checkout", args=[self.plan.pk])
        first = self.client.post(url, payload)
        second = self.client.post(url, payload)
        self.assertEqual(first.url, second.url)
        sub = Subscription.objects.get()
        self.assertEqual((CheckoutReceipt.objects.count(), Invoice.objects.count(), sub.acceptances.count()), (1, 1, 1))

    def test_checkout_token_cannot_be_reused_with_different_options(self):
        self.client.force_login(self.user)
        payload = self.order()
        url = reverse("store:checkout", args=[self.plan.pk])
        self.client.post(url, payload)
        self.client.post(url, {**payload, "quantity": 2})
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(Subscription.objects.get().lines.get().quantity, 1)

    def test_checkout_rollback_removes_lines_consent_and_email(self):
        self.client.force_login(self.user)
        from core.models import Outbox
        before = Outbox.objects.count()
        with patch("store.services.contracts.accept", side_effect=RuntimeError("failed acceptance")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order())
        self.assertEqual((Subscription.objects.count(), CheckoutReceipt.objects.count(), Outbox.objects.count()), (0, 0, before))

    def test_invalid_quantities_and_variants_never_create_orders(self):
        self.client.force_login(self.user)
        for quantity in ("oops", "1.5", "-2", "1000001", "9" * 100):
            with self.subTest(quantity=quantity):
                response = self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order(quantity=quantity))
                self.assertEqual(response.status_code, 302)
        product = Product.objects.create(name="Suite", sales_price=500, published=True)
        self.plan.products.add(product)
        response = self.client.get(reverse("store:plan", args=[self.plan.pk]), {f"variant_{product.pk}": "bad"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose an available variant")
        self.assertEqual(Subscription.objects.count(), 0)

    def test_checkout_token_is_bound_to_the_customer_and_plan(self):
        self.client.force_login(self.user)
        for token in ("tampered", storefront.checkout_token(self.u["admin"], self.plan)):
            self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order(checkout_token=token))
        self.assertEqual(Subscription.objects.count(), 0)

    def test_invalid_initial_selection_keeps_a_token_for_correcting_and_retrying(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("store:plan", args=[self.plan.pk]), {"quantity": "bad"})
        self.assertContains(response, 'name="checkout_token"')
        self.assertTrue(response.context["selection_error"])
        token = response.context["checkout_token"]
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order(checkout_token=token, quantity=2))
        self.assertEqual(Subscription.objects.get().lines.get().quantity, 2)

    def test_missing_consent_redirect_does_not_expose_csrf_or_signature(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("store:checkout", args=[self.plan.pk]),
                                    {"quantity": 2, "csrfmiddlewaretoken": "private-token", "signed_name": "Private Name"})
        self.assertNotIn("private-token", response.url)
        self.assertNotIn("Private", response.url)
        self.assertIn("quantity=2", response.url)

    def test_development_wipe_accounts_for_checkout_receipts(self):
        from core.management.commands.seed_demo import Command
        self.client.force_login(self.user)
        self.client.post(reverse("store:checkout", args=[self.plan.pk]), self.order())
        self.assertEqual(CheckoutReceipt.objects.count(), 1)
        Command().wipe()
        self.assertFalse(CheckoutReceipt.objects.exists())
        self.assertFalse(Subscription.objects.exists())
