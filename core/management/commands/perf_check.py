import statistics
import time

from django.core.management.base import BaseCommand
from django.test import Client
from django.urls import reverse

from accounts.models import User
from billing.models import Invoice
from subscriptions.models import Subscription

BUDGET = 2.0


class Command(BaseCommand):
    help = "Time key pages as the admin (full request/response cycle through the test client) against the current database."

    def add_arguments(self, p):
        p.add_argument("--runs", type=int, default=5)

    def handle(self, *a, runs=5, **o):
        c = Client(HTTP_HOST="localhost")
        c.force_login(User.objects.filter(role="admin").first())
        sub, inv = Subscription.objects.first(), Invoice.objects.first()
        pages = {
            "dashboard": reverse("core:dashboard"), "reports": reverse("core:reports"),
            "reports (overdue filter, plan)": reverse("core:reports") + "?view=overdue", "subscriptions": reverse("subscriptions:list"),
            "subscriptions (page 40)": reverse("subscriptions:list") + "?page=40", "subscriptions (search)": reverse("subscriptions:list") + "?q=Bulk Customer 77",
            "subscriptions (active)": reverse("subscriptions:list") + "?state=active", "invoices": reverse("billing:list"),
            "invoices (overdue)": reverse("billing:list") + "?status=overdue", "payments": reverse("billing:payments"),
            "customers": reverse("core:customers"), "subscription detail": reverse("subscriptions:detail", args=[sub.pk]),
            "invoice detail": reverse("billing:detail", args=[inv.pk])}
        self.stdout.write(f"{Subscription.objects.count()} subscriptions · {Invoice.objects.count()} invoices")
        worst = 0
        for name, url in pages.items():
            times = []
            for _ in range(runs):
                t = time.perf_counter()
                r = c.get(url)
                times.append(time.perf_counter() - t)
                assert r.status_code == 200, (url, r.status_code)
            worst = max(worst, max(times))
            self.stdout.write(f"{name:34} median {statistics.median(times)*1000:6.0f} ms   max {max(times)*1000:6.0f} ms")
        self.stdout.write((self.style.SUCCESS if worst < BUDGET else self.style.ERROR)(f"worst {worst*1000:.0f} ms (budget {BUDGET*1000:.0f} ms)"))
