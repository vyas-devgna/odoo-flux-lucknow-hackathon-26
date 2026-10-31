import statistics
import time

from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from accounts.models import Customer, User
from billing.models import Invoice
from subscriptions.models import Subscription

BUDGET = 2.0


class Command(BaseCommand):
    help = "Time key pages as the admin (full request/response cycle through the test client) against the current database."

    def add_arguments(self, p):
        p.add_argument("--runs", type=int, default=5)
        p.add_argument("--cold", action="store_true", help="Clear the report cache before every request (worst case).")

    def handle(self, *a, runs=5, cold=False, **o):
        if not 1 <= runs <= 100:
            raise CommandError("Choose between 1 and 100 runs.")
        admin = User.objects.filter(role="admin", is_active=True).first()
        sub, inv = Subscription.objects.first(), Invoice.objects.first()
        if admin is None or sub is None or inv is None:
            raise CommandError("Run seed_demo first; performance checks need an administrator, subscription, and invoice.")
        c = Client(HTTP_HOST="localhost")
        c.force_login(admin)
        c.get(reverse("core:dashboard"))  # warm any caches the way a second visit would
        from billing.models import Payment
        cust = Customer.objects.exclude(email__startswith="bulk").first()
        pages = {
            "overview": reverse("core:dashboard"), "revenue report": reverse("core:reports"), "MRR report": reverse("core:report_mrr"), "retention report": reverse("core:report_retention"),
            "receivables report": reverse("core:report_receivables"), "breakdown (by customer)": reverse("core:report_breakdown") + "?by=customer",
            "subscriptions": reverse("subscriptions:list"), "subscriptions (page 40)": reverse("subscriptions:list") + "?page=40", "subscriptions (search)": reverse("subscriptions:list") + "?q=Bulk Customer 77",
            "subscriptions (active, sorted)": reverse("subscriptions:list") + "?state=active&sort=-customer", "quotations": reverse("subscriptions:quotes"), "pipeline": reverse("subscriptions:pipeline"),
            "renewals": reverse("subscriptions:renewals"), "changes": reverse("subscriptions:changes"), "invoices": reverse("billing:list"), "invoices (overdue)": reverse("billing:list") + "?status=overdue",
            "invoices (search)": reverse("billing:list") + "?q=INV/B/10", "payments": reverse("billing:payments"), "customers": reverse("core:customers"), "customer search": reverse("core:search") + "?q=Bulk Customer 5",
            "subscription detail": reverse("subscriptions:detail", args=[sub.pk]), "invoice detail": reverse("billing:detail", args=[inv.pk]), "billing runs": reverse("billing:runs"),
            "audit log": reverse("core:audit"), "invoice PDF": reverse("billing:pdf", args=[inv.pk]), "customer export (CSV)": reverse("core:export", args=["customers"])}
        self.stdout.write(f"{Subscription.objects.count()} subscriptions · {Invoice.objects.count()} invoices")
        worst = 0
        for name, url in pages.items():
            times = []
            for _ in range(runs):
                if cold:
                    from django.core.cache import cache
                    cache.clear()
                t = time.perf_counter()
                r = c.get(url)
                times.append(time.perf_counter() - t)
                assert r.status_code == 200, (url, r.status_code)
            worst = max(worst, max(times))
            self.stdout.write(f"{name:34} median {statistics.median(times)*1000:6.0f} ms   max {max(times)*1000:6.0f} ms")
        self.stdout.write((self.style.SUCCESS if worst < BUDGET else self.style.ERROR)(f"worst {worst*1000:.0f} ms (budget {BUDGET*1000:.0f} ms)"))
        if worst >= BUDGET:
            raise CommandError("The measured response-time budget was exceeded.")
