"""Demo data. `seed_demo` builds a small realistic book by replaying months through the real billing engine;
`seed_demo --bulk 5000` adds thousands of subscriptions (direct inserts) to measure page response times."""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Customer, User
from billing import services as billing
from billing.models import BillingRun, Invoice, InvoiceLine, Payment
from catalog.models import Discount, Plan, Product, ProductVariant, QuotationTemplate, TemplateLine, Tax
from subscriptions import services as subs
from subscriptions.models import Subscription, SubscriptionLine, add_interval

PASSWORDS = {"admin@brightloop.test": "Admin@12345", "staff@brightloop.test": "Staff@12345", "maya@acme.test": "Customer@123"}


class Command(BaseCommand):
    help = "Load demo data (idempotent for users/catalogue; refuses to re-seed subscriptions)."

    def add_arguments(self, p):
        p.add_argument("--bulk", type=int, default=0, help="Add N extra subscriptions for performance testing.")

    def handle(self, *a, bulk=0, **o):
        if bulk:
            return self.bulk(bulk)
        if Subscription.objects.exists():
            self.stdout.write("Subscriptions already exist; skipping demo seed.")
            return
        today = timezone.localdate()
        from core.models import CompanySettings
        co = CompanySettings.get()
        co.name, co.address, co.tax_id, co.email = "Brightloop Software", "21 Hazratganj, Lucknow 226001", "09ABCDE1234F1Z5", "billing@brightloop.test"
        co.bank_details = "Brightloop Software · A/C 1234567890 · IFSC HDFC0000123 · UPI brightloop@hdfc"
        co.save()
        admin = User.objects.create_user("admin@brightloop.test", PASSWORDS["admin@brightloop.test"], name="Aarav Admin", role="admin", is_staff=True, is_superuser=True)
        User.objects.create_user("staff@brightloop.test", PASSWORDS["staff@brightloop.test"], name="Sana Sales", role="staff")
        gst, _ = Tax.objects.get_or_create(name="GST", defaults=dict(rate=18, is_default=True))
        Tax.objects.get_or_create(name="GST reduced", defaults=dict(rate=5))
        suite = Product.objects.create(name="Brightloop Suite", type="software", sales_price=1200, cost_price=400, notes="Core subscription software")
        for v, x in (("Standard", 0), ("Premium", 560), ("Enterprise", 1500)):
            ProductVariant.objects.create(product=suite, attribute="Brand", value=v, extra_price=x)
        analytics = Product.objects.create(name="Analytics Pack", type="addon", sales_price=300, cost_price=90)
        support = Product.objects.create(name="Priority Support", type="addon", sales_price=500, cost_price=200)
        storage = Product.objects.create(name="Extra Storage 100GB", type="addon", sales_price=150, cost_price=40)
        onboarding = Product.objects.create(name="Onboarding Service", type="service", sales_price=2500, cost_price=900)
        starter = Plan.objects.create(name="Starter", price=999, interval="monthly", min_quantity=1)
        business = Plan.objects.create(name="Business", price=2499, interval="monthly", min_quantity=3)
        team_y = Plan.objects.create(name="Team Annual", price=24990, interval="yearly", min_quantity=1, allow_pause=False, allow_early_close=False)
        pilot = Plan.objects.create(name="Weekly Pilot", price=199, interval="weekly", min_quantity=1, auto_close=True)
        for p in (starter, business, team_y, pilot):
            p.products.set([suite, analytics, support, storage] if p != pilot else [analytics])
        t1 = QuotationTemplate.objects.create(name="Business + Premium Suite", plan=business, validity_days=30)
        TemplateLine.objects.create(template=t1, product=suite, variant=suite.variants.get(value="Premium"), quantity=3)
        TemplateLine.objects.create(template=t1, product=support, quantity=1)
        t2 = QuotationTemplate.objects.create(name="Starter Kit", plan=starter, validity_days=14)
        TemplateLine.objects.create(template=t2, product=suite, quantity=1)
        Discount.objects.create(name="Volume 10%", kind="percent", value=10, min_quantity=5, start_date=today - timedelta(days=400))
        Discount.objects.create(name="Welcome ₹500", kind="fixed", value=500, min_purchase=1000, start_date=today - timedelta(days=200), usage_limit=3)
        Discount.objects.create(name="Spring 20% (ended)", kind="percent", value=20, start_date=today - timedelta(days=300), end_date=today - timedelta(days=120))
        sd = Discount.objects.create(name="Suite loyalty 15%", kind="percent", value=15, start_date=today - timedelta(days=400))
        sd.scope = "products"
        sd.save()
        sd.products.set([suite])

        rows = [("Maya Rao", "Acme Retail", "maya@acme.test", business, 6, t1, "net30"),
                ("Dev Malhotra", "Northwind Traders", "dev@northwind.test", starter, 5, t2, "net15"),
                ("Isha Kapoor", "Lumen Studio", "isha@lumen.test", team_y, 11, None, "net30"),
                ("Rohan Gupta", "Gupta Hardware", "rohan@gupta.test", starter, 4, t2, "immediate"),
                ("Neha Iyer", "Bluepeak Labs", "neha@bluepeak.test", business, 3, t1, "net15"),
                ("Karan Shah", "Shah & Sons", "karan@shah.test", starter, 2, None, "net15"),
                ("Anita Desai", "Desai Foods", "anita@desai.test", pilot, 1, None, "immediate"),
                ("Vikram Sethi", "Sethi Logistics", "vikram@sethi.test", business, 8, t1, "net60")]
        created = []
        for name, company, email, plan, months_ago, tpl, terms in rows:
            c = Customer.objects.create(name=name, company=company, email=email, address="12 MG Road, Lucknow 226001", phone="+91 98765 43210")
            if email == "maya@acme.test":
                u = User.objects.create_user(email, PASSWORDS[email], name=name, role="customer")
                c.user = u
                c.save()
            start = add_interval(today, "monthly", -months_ago) if plan.interval != "weekly" else today - timedelta(weeks=9)
            s = subs.create_subscription(customer=c, plan=plan, template=tpl, start_date=start, payment_terms=terms, user=admin,
                                         expiry_date=None if plan.interval != "weekly" else today + timedelta(days=60))
            if plan == business:  # bulk seats so the volume discount is demonstrably eligible
                s.lines.filter(label=plan.name).update(quantity=5)
            created.append(s)
        # one quotation still in flight and one in draft
        q = subs.create_subscription(customer=Customer.objects.get(email="dev@northwind.test"), plan=business, template=t1, user=admin)
        subs.send_quotation(q)
        subs.create_subscription(customer=Customer.objects.get(email="isha@lumen.test"), plan=starter, template=t2, user=admin)
        for s in created:
            subs.send_quotation(s)
            subs.confirm(s)
            subs.activate(s)
        Subscription.objects.get(customer__email="neha@bluepeak.test").lines.filter(product=suite).update(quantity=1)
        # Replay the past through the real engine, month by month
        d = today - timedelta(days=int(30.4 * 8))
        while d <= today:
            billing.run_billing(d, trigger="demo-replay")
            d += timedelta(days=7)
        billing.run_billing(today, trigger="demo-replay")
        BillingRun.objects.filter(trigger="demo-replay").delete()
        BillingRun.objects.create(as_of=today, trigger="scheduler", created=0, finished_at=timezone.now())
        rnd = random.Random(7)
        stale = {"dev@northwind.test", "rohan@gupta.test", "karan@shah.test"}  # left unpaid on purpose -> overdue
        for inv in Invoice.objects.order_by("id"):
            newest = not Invoice.objects.filter(subscription=inv.subscription, id__gt=inv.id).exists()
            if newest and inv.issue_date >= today - timedelta(days=3):
                continue  # latest invoices stay as drafts for the demo ("review and confirm")
            billing.confirm_invoice(inv)
            if newest and inv.customer.email in {'neha@bluepeak.test', 'vikram@sethi.test', 'dev@northwind.test'} and inv.issue_date >= today - timedelta(days=40):
                continue  # recent, still within terms: outstanding but not overdue
            if inv.customer.email in stale and rnd.random() < 0.7 and not newest:
                continue
            if inv.customer.email in stale and newest:
                continue
            if rnd.random() < 0.12:
                billing.record_payment(inv, amount=(inv.total / 2).quantize(Decimal("0.01")), method="upi", date=inv.issue_date + timedelta(days=3), note="Part payment", user=admin)
                continue
            if inv.customer.email in stale:
                continue
            billing.record_payment(inv, amount=inv.total, method=rnd.choice(["bank", "upi", "card", "cheque"]),
                                   date=min(today, inv.issue_date + timedelta(days=rnd.randint(1, 12))), note="Auto-matched", user=admin)
        self.stdout.write(self.style.SUCCESS("Demo ready. Logins: " + ", ".join(f"{e} / {p}" for e, p in PASSWORDS.items())))

    @transaction.atomic
    def bulk(self, n):
        """Direct inserts: N active subscriptions, each with 1-6 historic invoices and mostly settled payments."""
        rnd = random.Random(42)
        today = timezone.localdate()
        plans = list(Plan.objects.filter(interval__in=["monthly", "yearly"]))
        tax = Tax.default()
        base = Customer.objects.count()
        customers = Customer.objects.bulk_create([Customer(name=f"Bulk Customer {i}", email=f"bulk{base + i}@example.test", company=f"Co {i}") for i in range(n)])
        customers = list(Customer.objects.filter(email__startswith="bulk").order_by("-id")[:n])
        subs_ = []
        for i, c in enumerate(customers):
            plan = rnd.choice(plans)
            k = rnd.randint(1, 6)
            start = add_interval(today, plan.interval, -k) + timedelta(days=rnd.randint(0, 3))
            subs_.append(Subscription(number=f"BLK{base + i:07d}", customer=c, plan=plan, state="active", start_date=start,
                                      expiry_date=add_interval(start, "yearly", 1), payment_terms="net15", periods_billed=k,
                                      next_invoice_date=add_interval(start, plan.interval, k)))
        Subscription.objects.bulk_create(subs_, batch_size=500)
        subs_ = list(Subscription.objects.filter(number__startswith="BLK").order_by("-id")[:n])
        SubscriptionLine.objects.bulk_create([SubscriptionLine(subscription=s, label=s.plan.name, quantity=rnd.randint(1, 20),
                                                               unit_price=s.plan.price, tax=tax) for s in subs_], batch_size=500)
        lines = {l.subscription_id: l for l in SubscriptionLine.objects.filter(subscription__in=subs_)}
        invoices, meta = [], []
        for s in subs_:
            l = lines[s.pk]
            net = l.amount
            tx = l.tax_amount
            for p in range(s.periods_billed):
                ps = add_interval(s.start_date, s.plan.interval, p)
                due = ps + timedelta(days=15)
                paid = rnd.random() < 0.7
                state = "paid" if paid else "confirmed"
                invoices.append(Invoice(number=f"INV/B/{s.pk}-{p}", subscription=s, customer=s.customer, state=state, period_start=ps,
                                        period_end=add_interval(s.start_date, s.plan.interval, p + 1) - timedelta(days=1), issue_date=ps, due_date=due,
                                        bill_to_name=s.customer.display_name, bill_to_email=s.customer.email, subtotal=net, tax_total=tx,
                                        total=net + tx, amount_paid=net + tx if paid else 0))
                meta.append((l, net, tx, paid))
        Invoice.objects.bulk_create(invoices, batch_size=500)
        created = {i.number: i for i in Invoice.objects.filter(number__startswith="INV/B/")}
        il, pay = [], []
        for inv, (l, net, tx, paid) in zip(invoices, meta):
            real = created[inv.number]
            il.append(InvoiceLine(invoice=real, description=l.label, quantity=l.quantity, unit_price=l.unit_price, tax_name="GST", tax_rate=18, tax_amount=tx, amount=net))
            if paid:
                pay.append(Payment(invoice=real, amount=net + tx, method=rnd.choice(["bank", "upi", "card"]), date=min(today, real.issue_date + timedelta(days=rnd.randint(1, 20)))))
        InvoiceLine.objects.bulk_create(il, batch_size=500)
        Payment.objects.bulk_create(pay, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Added {n} subscriptions, {len(invoices)} invoices, {len(pay)} payments."))
