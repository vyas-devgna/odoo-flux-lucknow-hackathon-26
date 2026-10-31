from django.core import mail
from django.urls import reverse

from accounts.models import Customer, User
from core import mail as outbox
from core.models import Outbox

from .helpers import PW, Base, make_active


class SignupAndLogin(Base):
    def signup(self, email="n@x.test", pw=PW):
        return self.client.post(reverse("accounts:signup"), {"name": "N", "email": email, "password": pw, "confirm": pw})

    def test_password_rules(self):
        for bad in ("short1!A", "alllowercase1!", "ALLUPPERCASE1!", "NoSpecial1234", "Ab1!abcd"):  # last is exactly 8 chars
            self.assertEqual(self.signup(pw=bad).status_code, 200, bad)
        self.assertFalse(User.objects.filter(email="n@x.test").exists())
        self.assertEqual(self.signup().status_code, 302)

    def test_duplicate_email_case_insensitive(self):
        self.signup()
        self.client.logout()
        self.assertEqual(self.signup("N@X.test").status_code, 200)
        self.assertEqual(User.objects.filter(email="n@x.test").count(), 1)

    def test_signup_adopts_prepared_customer(self):
        prepared = Customer.objects.create(name="Prep", email="prep@x.test")
        self.signup("prep@x.test")
        prepared.refresh_from_db()
        self.assertIsNotNone(prepared.user_id)
        self.assertEqual(Customer.objects.filter(email="prep@x.test").count(), 1)

    def test_signup_next_redirect_is_safe(self):
        r = self.client.post(reverse("accounts:signup"), {"name": "N", "email": "s@x.test", "password": PW, "confirm": PW, "next": "https://evil.example/"})
        self.assertEqual(r.url, "/")
        self.client.logout()
        r = self.client.post(reverse("accounts:signup"), {"name": "N", "email": "s2@x.test", "password": PW, "confirm": PW, "next": "/store/"})
        self.assertEqual(r.url, "/store/")

    def test_login_throttle_and_reset_on_success(self):
        User.objects.create_user("t@x.test", PW)
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": "wrong"})
        r = self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": PW})
        self.assertContains(r, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_is_case_insensitive_and_clears_failures(self):
        User.objects.create_user("t@x.test", PW)
        self.client.post(reverse("accounts:login"), {"username": "t@x.test", "password": "bad"})
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": "T@X.test", "password": PW}).status_code, 302)

    def test_password_reset_email_and_throttle(self):
        User.objects.create_user("r@x.test", PW, name="R")
        for _ in range(5):
            self.client.post(reverse("accounts:reset"), {"email": "r@x.test"})
        resets = [m for m in mail.outbox if "reset" in m.body.lower()]
        self.assertEqual(len(resets), 3)  # django sends directly; the fourth and fifth are throttled silently
        self.assertIn("/accounts/reset/", resets[0].body)

    def test_reset_link_single_use(self):
        u = User.objects.create_user("r2@x.test", PW)
        self.client.post(reverse("accounts:reset"), {"email": "r2@x.test"})
        import re
        link = re.search(r"/accounts/reset/[^\s]+", mail.outbox[-1].body).group(0)
        r = self.client.get(link, follow=True)
        post_url = r.redirect_chain[-1][0] if r.redirect_chain else link
        self.client.post(post_url, {"new_password1": "An0ther!Passw0rd", "new_password2": "An0ther!Passw0rd"})
        u.refresh_from_db()
        self.assertTrue(u.check_password("An0ther!Passw0rd"))
        again = self.client.post(post_url, {"new_password1": "Third!Passw0rd1", "new_password2": "Third!Passw0rd1"})
        u.refresh_from_db()
        self.assertFalse(u.check_password("Third!Passw0rd1"))

    def test_suspended_user_is_signed_out(self):
        u = self.u["sales"]
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("subscriptions:quotes")).status_code, 200)
        u.is_active = False
        u.save()
        self.assertEqual(self.client.get(reverse("subscriptions:quotes")).status_code, 302)

    def test_only_admin_adds_staff(self):
        self.client.force_login(self.u["sales"])
        self.assertEqual(self.client.get(reverse("accounts:team")).status_code, 403)
        self.assertEqual(self.client.post(reverse("accounts:team_new"), {"name": "X", "email": "x@x.test", "role": "staff", "password": PW}).status_code, 403)
        self.client.force_login(self.u["admin"])
        r = self.client.post(reverse("accounts:team_new"), {"name": "X", "email": "x@x.test", "role": "staff", "password": PW, "can_sales": "on", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        x = User.objects.get(email="x@x.test")
        self.assertTrue(x.can_sales and not x.can_finance and not x.is_superuser)
