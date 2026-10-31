from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Customer, User
from billing import services as billing
from catalog.models import Plan, Product, Tax
from subscriptions import services as subs
from subscriptions.models import Subscription, add_interval

PW = "Str0ng!Passw0rd"
TODAY = timezone.localdate


def make_world(interval="monthly", price=1000, **plan_kw):
    Tax.objects.get_or_create(name="GST", defaults=dict(rate=18, is_default=True))
    plan = Plan.objects.create(name=f"{interval.title()} plan", price=price, interval=interval, **plan_kw)
    cust = Customer.objects.create(name="Alex", email=f"alex{Customer.objects.count()}@x.test")
    return plan, cust


def make_active(plan, cust, start=None, qty=1, user=None, **kw):
    start = start or TODAY()
    s = subs.create_subscription(customer=cust, plan=plan, start_date=start, quantity=qty, user=user, **kw)
    subs.send_quotation(s)
    s = subs.accept(Subscription.objects.get(pk=s.pk), actor=user, signed_name="Alex")
    s = Subscription.objects.get(pk=s.pk)
    if s.state == Subscription.CONFIRMED:
        subs.activate(s, actor=user)
    return Subscription.objects.get(pk=s.pk)


def users():
    return {
        "admin": User.objects.create_user("admin@x.test", PW, role="admin", name="Admin"),
        "sales": User.objects.create_user("sales@x.test", PW, role="staff", name="Sales", can_sales=True, can_billing=False, can_finance=False),
        "billing": User.objects.create_user("bill@x.test", PW, role="staff", name="Bill", can_sales=False, can_billing=True, can_finance=False),
        "finance": User.objects.create_user("fin@x.test", PW, role="staff", name="Fin", can_sales=False, can_billing=False, can_finance=True),
    }


class Base(TestCase):
    def setUp(self):
        self.today = TODAY()
        self.plan, self.cust = make_world()
        self.u = users()
