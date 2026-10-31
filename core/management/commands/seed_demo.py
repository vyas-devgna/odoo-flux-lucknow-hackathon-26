"""Demo data, built by replaying real months through the real services (so every number reconciles).

  seed_demo               idempotent: refuses to run twice
  seed_demo --expand      safely add the keyed 26-contract demo extension
  seed_demo --reset       development only (DEBUG=1): wipes business data first
  seed_demo --bulk 5000   adds thousands of directly-inserted subscriptions for performance testing
"""
import random
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from accounts.models import Customer, User
from billing import services as billing
from billing.models import BillingPeriod, Invoice, InvoiceLine, Payment
from catalog.models import Category, Discount, Plan, PlanTier, Product, ProductVariant, QuotationTemplate, TemplateLine, Tax
from core import audit
from core.models import CompanySettings, Notification, Task
from subscriptions import services as subs
from subscriptions.models import Subscription, SubscriptionChange, SubscriptionLine, add_interval

PW = {"admin@brightloop.test": "Admin@12345", "staff@brightloop.test": "Staff@12345", "finance@brightloop.test": "Finance@12345", "catalog@brightloop.test": "Catalog@12345", "maya@acme.test": "Customer@123"}

ORGS = [("Maya Rao", "Acme Retail"), ("Dev Malhotra", "Northwind Traders"), ("Isha Kapoor", "Lumen Studio"), ("Rohan Gupta", "Gupta Hardware"), ("Neha Iyer", "Bluepeak Labs"),
        ("Karan Shah", "Shah & Sons"), ("Anita Desai", "Desai Foods"), ("Vikram Sethi", "Sethi Logistics"), ("Priya Nair", "Nair Analytics"), ("Arjun Mehta", "Mehta Textiles"),
        ("Sneha Pillai", "Pillai Pharma"), ("Rahul Verma", "Verma Motors"), ("Kavya Reddy", "Reddy Robotics"), ("Aman Khanna", "Khanna Capital"), ("Tara Bose", "Bose Bakery"),
        ("Imran Qureshi", "Qureshi Exports"), ("Divya Menon", "Menon Health"), ("Sahil Arora", "Arora Cycles"), ("Pooja Joshi", "Joshi Interiors"), ("Nikhil Rao", "Rao Dental"),
        ("Meera Das", "Das Cloudworks"), ("Yash Tiwari", "Tiwari Media"), ("Ritu Saxena", "Saxena Schools"), ("Farhan Ali", "Ali Couriers"), ("Lakshmi Iyer", "Iyer Tea Estates")]

EXPANSION_PREFIX = "seed-demo:v2:"
EXPANSION_ORGS = [
    ("Aditi Verma", "Cedarline Design Co.", "Design", "Indira Nagar, Lucknow"),
    ("Kabir Sinha", "Riverbend Foods", "Food distribution", "Gomti Nagar, Lucknow"),
    ("Sana Mirza", "Atlas Field Services", "Field services", "Aliganj, Lucknow"),
    ("Vihaan Joshi", "Copperleaf Learning", "Education", "Hazratganj, Lucknow"),
    ("Ananya Bose", "Monsoon Care Network", "Healthcare", "Vibhuti Khand, Lucknow"),
    ("Reyansh Kulkarni", "Blue Orchard Supply", "Wholesale", "Kanpur Road, Lucknow"),
    ("Mira Thomas", "Foundry Works India", "Manufacturing", "Amausi, Lucknow"),
    ("Ibrahim Khan", "Paper Kite Media", "Media", "Gomti Nagar, Lucknow"),
    ("Tara Chawla", "Northstar Mobility", "Mobility", "Mahanagar, Lucknow"),
    ("Arjun Bhat", "Civic Thread Studio", "Professional services", "Sushant Golf City, Lucknow"),
    ("Naina Kapoor", "Sunroom Hospitality", "Hospitality", "Hazratganj, Lucknow"),
    ("Devika Nair", "Fieldnote Research", "Research", "Jankipuram, Lucknow"),
    ("Raghav Menon", "Mosaic Trade Partners", "Trade", "Alambagh, Lucknow"),
]


def _expansion_email(index):
    company = EXPANSION_ORGS[index - 1][1]
    domain = "".join(ch.lower() if ch.isalnum() else "-" for ch in company).strip("-")
    return f"accounts@{domain}.test"


class Command(BaseCommand):
    help = "Load reproducible demo data (see module docstring)."

    def add_arguments(self, p):
        p.add_argument("--bulk", type=int, default=0)
        p.add_argument("--reset", action="store_true")
        p.add_argument("--expand", action="store_true", help="add only the stable demo extension to a recognized demo seed")

    def handle(self, *a, bulk=0, reset=False, expand=False, **o):
        if expand and (bulk or reset):
            raise CommandError("--expand cannot be combined with --bulk or --reset.")
        if expand:
            with override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):
                return self.expand_existing()
        if bulk:
            return self.bulk(bulk)
        if reset:
            if not settings.DEBUG:
                raise CommandError("--reset is for development only (DJANGO_DEBUG=1).")
            self.wipe()
        if Subscription.objects.exists():
            self.stdout.write("Subscriptions already exist; skipping demo seed. Use --reset in development to start over.")
            return
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):
            self.build()
        self.stdout.write(self.style.SUCCESS("Demo ready. Logins: " + ", ".join(f"{e} / {p}" for e, p in PW.items())))

    def wipe(self):
        from billing.models import BillingFailure, BillingRun, DiscountUse, PaymentEvent, PaymentLink
        from core.models import AuditLog, AutomationLog, Outbox
        from store.models import CheckoutReceipt
        for m in (CheckoutReceipt, PaymentLink, PaymentEvent, Payment, DiscountUse, InvoiceLine, BillingPeriod, BillingFailure, Invoice, BillingRun, SubscriptionChange, SubscriptionLine, Subscription,
                  TemplateLine, QuotationTemplate, Discount, PlanTier, ProductVariant, Product, Plan, Category, Customer, AuditLog, AutomationLog, Outbox, Notification, Task):
            m.objects.all().delete()
        User.objects.all().delete()

    @transaction.atomic
    def build(self, *, include_expansion=True):
        rnd = random.Random(2026)
        today = timezone.localdate()
        co = CompanySettings.get()
        co.name, co.address, co.tax_id, co.email = "Brightloop Studio", "21 Hazratganj, Lucknow 226001", "09ABCDE1234F1Z5", "billing@brightloop.test"
        co.bank_details = "Brightloop Studio · A/C 1234567890 · IFSC HDFC0000123 · UPI brightloop@hdfc"
        co.save()
        admin = User.objects.create_user("admin@brightloop.test", PW["admin@brightloop.test"], name="Aarav Admin", role="admin", is_staff=True, is_superuser=True)
        sana = User.objects.create_user("staff@brightloop.test", PW["staff@brightloop.test"], name="Sana Sales", role="staff", can_sales=True, can_billing=True)
        rohit = User.objects.create_user("finance@brightloop.test", PW["finance@brightloop.test"], name="Rohit Finance", role="staff", can_sales=False, can_billing=True, can_finance=True)
        meera = User.objects.create_user("catalog@brightloop.test", PW["catalog@brightloop.test"], name="Meera Catalog", role="staff", can_sales=True, can_billing=False, can_catalog=True)

        gst = Tax.objects.create(name="GST", rate=18, is_default=True)
        gst5 = Tax.objects.create(name="GST reduced", rate=5)
        vat = Tax.objects.create(name="Inclusive levy", rate=12, inclusive=True)
        cat = {n: Category.objects.create(name=n) for n in ("Software", "Services", "Add-ons", "Hardware")}
        suite = Product.objects.create(name="Brightloop Suite", sku="BL-SUITE", type="software", category=cat["Software"], sales_price=1200, cost_price=400, description="The core platform", published=True)
        for v, x in (("Standard", 0), ("Premium", 560), ("Enterprise", 1500)):
            ProductVariant.objects.create(product=suite, attribute="Brand", value=v, extra_price=x)
        analytics = Product.objects.create(name="Analytics Pack", sku="BL-ANALYTICS", type="addon", category=cat["Add-ons"], sales_price=300, cost_price=90, published=True, description="Dashboards and exports")
        support = Product.objects.create(name="Priority Support", sku="BL-SUPPORT", type="addon", category=cat["Add-ons"], sales_price=500, cost_price=200, published=True, description="4-hour response")
        storage = Product.objects.create(name="Extra Storage 100GB", sku="BL-STORAGE", type="addon", category=cat["Add-ons"], sales_price=150, cost_price=40, uom="100GB", published=True)
        api = Product.objects.create(name="API Access", sku="BL-API", type="addon", category=cat["Add-ons"], sales_price=400, cost_price=60, tax=gst5, published=True)
        onboard = Product.objects.create(name="Onboarding Service", sku="BL-ONBOARD", type="service", category=cat["Services"], sales_price=2500, cost_price=900, recurring=False, uom="project")
        sensor = Product.objects.create(name="Field Sensor Kit", sku="BL-SENSOR", type="physical", category=cat["Hardware"], sales_price=1120, cost_price=600, tax=vat, uom="kit")

        starter = Plan.objects.create(name="Starter", price=999, interval="monthly", min_quantity=1, published=True, description="For small teams getting started", activation="acceptance", allow_pause=True)
        business = Plan.objects.create(name="Business", price=2499, interval="monthly", min_quantity=3, published=True, description="Per-seat pricing for growing teams", payment_terms="net30", renewal_policy="auto", activation="manual")
        PlanTier.objects.create(plan=business, min_quantity=10, unit_price=2199)
        team_y = Plan.objects.create(name="Team Annual", price=24990, interval="yearly", min_quantity=1, published=True, allow_pause=False, payment_terms="net30", renewal_policy="auto", activation="payment", min_duration_periods=1)
        weekly = Plan.objects.create(name="Weekly Pilot", price=199, interval="weekly", min_quantity=1, payment_terms="immediate")
        daily = Plan.objects.create(name="Daily Trial", price=49, interval="daily", min_quantity=1, published=True, payment_terms="immediate", cancel_policy="anytime", activation="acceptance", description="Pay only for the days you use")
        enterprise = Plan.objects.create(name="Enterprise", price=5200, interval="yearly", min_quantity=5, payment_terms="net60", renewal_policy="manual", cancel_policy="period_end", min_duration_periods=1, self_serve_cancel=False)
        for p in (starter, business, team_y, enterprise):
            p.products.set([suite, analytics, support, storage, api, onboard])
        weekly.products.set([analytics]); daily.products.set([analytics, storage])
        t1 = QuotationTemplate.objects.create(name="Business + Premium Suite", plan=business, validity_days=30)
        TemplateLine.objects.create(template=t1, product=suite, variant=suite.variants.get(value="Premium"), quantity=3)
        TemplateLine.objects.create(template=t1, product=support, quantity=1)
        TemplateLine.objects.create(template=t1, product=onboard, quantity=1)
        t2 = QuotationTemplate.objects.create(name="Starter kit", plan=starter, validity_days=14)
        TemplateLine.objects.create(template=t2, product=suite, quantity=1)
        t3 = QuotationTemplate.objects.create(name="Enterprise rollout", plan=enterprise, validity_days=45)
        TemplateLine.objects.create(template=t3, product=suite, variant=suite.variants.get(value="Enterprise"), quantity=5)
        TemplateLine.objects.create(template=t3, product=analytics, quantity=1)
        Discount.objects.create(name="Volume 10%", kind="percent", value=10, min_quantity=8, start_date=today - timedelta(days=500))
        Discount.objects.create(name="Welcome ₹500", kind="fixed", value=500, min_purchase=1000, start_date=today - timedelta(days=200), usage_limit=3)
        Discount.objects.create(name="Spring 20%", kind="percent", value=20, start_date=today - timedelta(days=300), end_date=today - timedelta(days=120))
        sd = Discount.objects.create(name="Suite loyalty 15%", kind="percent", value=15, scope="products", start_date=today - timedelta(days=500), usage_limit=20)
        sd.products.set([suite])
        ed = Discount.objects.create(name="Enterprise 8%", kind="percent", value=8, start_date=today - timedelta(days=500))
        ed.plans.set([enterprise])

        # customers
        custs = []
        for i, (name, company) in enumerate(ORGS):
            slug = company.split()[0].lower().replace("&", "and")
            email = f"{name.split()[0].lower()}@{slug}.test"
            c = Customer.objects.create(name=name, company=company, email=email, phone=f"+91 98{rnd.randint(10000000, 99999999)}", address=f"{rnd.randint(1, 99)} MG Road, Lucknow 2260{rnd.randint(10, 99)}",
                                        tax_id=f"09{company[:5].upper().replace(' ', 'X')}1234F1Z{i % 9}" if i % 3 == 0 else "", account_manager=sana if i % 2 else admin)
            if email.startswith("maya@"):
                email = "maya@acme.test"
                c.email = email
                c.save()
                c.user = User.objects.create_user(email, PW[email], name=name, role="customer")
                c.save()
            custs.append(c)

        # contracts: (customer index, plan, months ago, qty, template, behaviour, extras)
        plan_by = {"starter": starter, "business": business, "team": team_y, "weekly": weekly, "daily": daily, "ent": enterprise}
        spec = [(0, "business", 6, 5, t1, "good"), (1, "starter", 5, 1, t2, "late"), (2, "team", 11, 2, None, "good"), (3, "starter", 4, 1, t2, "bad"), (4, "business", 3, 4, t1, "partial"),
                (5, "starter", 2, 3, None, "bad"), (6, "weekly", 2, 1, None, "good"), (7, "business", 8, 12, t1, "good"), (8, "starter", 7, 2, t2, "good"), (9, "ent", 10, 6, t3, "good"),
                (10, "business", 9, 3, t1, "good"), (11, "starter", 1, 1, t2, "good"), (12, "team", 6, 1, None, "late"), (13, "business", 5, 4, t1, "good"), (14, "starter", 8, 1, t2, "good"),
                (15, "business", 4, 6, t1, "partial"), (16, "starter", 3, 2, None, "good"), (17, "daily", 0, 2, None, "good"), (18, "starter", 6, 1, t2, "good"), (19, "business", 7, 3, t1, "late"),
                (20, "team", 11, 3, None, "good"), (21, "starter", 5, 2, t2, "good"), (22, "business", 2, 8, t1, "good"), (23, "weekly", 1, 2, None, "late"), (24, "starter", 10, 1, t2, "good"),
                (0, "starter", 3, 2, t2, "good"), (4, "ent", 12, 5, t3, "good"), (7, "starter", 4, 4, None, "good"), (8, "business", 6, 3, None, "good"), (13, "starter", 5, 2, t2, "good"),
                (2, "starter", 4, 1, t2, "good"), (6, "starter", 2, 1, None, "good"), (9, "business", 5, 5, t1, "good"), (10, "starter", 6, 3, t2, "good"), (11, "business", 3, 6, t1, "good"),
                (14, "team", 9, 1, None, "good"), (15, "starter", 7, 2, t2, "good"), (16, "business", 4, 3, t1, "good"), (18, "starter", 5, 2, None, "good"), (20, "business", 8, 4, t1, "good"),
                (21, "starter", 3, 1, None, "good"), (3, "business", 2, 3, t1, "good"), (5, "team", 8, 1, None, "good"), (12, "starter", 4, 2, t2, "good")]
        created = []
        for n, (ci, pk, months, qty, tpl, beh) in enumerate(spec):
            plan = plan_by[pk]
            if pk == "daily":
                start = today - timedelta(days=22 + n % 5)
            elif pk == "weekly":
                start = today - timedelta(weeks=6 + months)
            else:
                start = add_interval(today, "monthly", -months) + timedelta(days=n % 3)
            plan_tpl = tpl if tpl and tpl.plan_id == plan.pk else None
            s = subs.create_subscription(customer=custs[ci], plan=plan, template=plan_tpl, start_date=start, quantity=max(qty, plan.min_quantity), user=sana if n % 2 else admin,
                                         expiry_date=(start + timedelta(days=75) if pk == "daily" else (today + timedelta(days=60) if pk == "weekly" else None)))
            if plan.min_quantity <= qty and pk == "business" and qty >= 8:
                pass
            if n % 7 == 0:
                s.renewal_policy = "auto"
                s.save(update_fields=["renewal_policy"])
            created.append((s, beh))
        # contracts that end soon (some will auto renew, some need a decision)
        for s, _ in created[:12]:
            if s.plan.interval == "monthly" and s.pk % 3 == 0:
                Subscription.objects.filter(pk=s.pk).update(expiry_date=today + timedelta(days=9 + s.pk % 25))

        # confirm + activate through the real services, effective on their start dates
        for s, _ in created:
            s = Subscription.objects.get(pk=s.pk)
            subs.send_quotation(s)
            subs.accept(Subscription.objects.get(pk=s.pk), actor=sana, signed_name=s.customer.name)
            s = Subscription.objects.get(pk=s.pk)
            if s.state == "confirmed":
                subs.activate(s, actor=sana)
        events = defaultdict(list)  # date -> [callables]
        behaviours = {s.pk: b for s, b in created}
        # events: upsell/expansion, pause, add-on, closures, in the past
        for s, _ in created[:30]:
            s = Subscription.objects.get(pk=s.pk)
            if s.state != "active":
                continue
            roll = s.pk % 11
            when = s.start_date + timedelta(days=40 + (s.pk * 7) % 60)
            if when >= today - timedelta(days=3):
                continue
            if roll in (1, 2) and s.plan.interval == "monthly":
                events[when].append(("seats", s.pk, s.lines.filter(is_plan_line=True).first().quantity + 2))
            elif roll == 3 and s.plan.interval == "monthly":
                events[when].append(("addon", s.pk, analytics.pk))
            elif roll == 4 and s.plan.interval == "monthly":
                events[when].append(("downgrade", s.pk, 0))
            elif roll == 5:
                events[when].append(("close", s.pk, 0))
        # replay time through the real billing engine
        first = min(s.start_date for s, _ in created)
        d = first
        rnd2 = random.Random(7)
        while d <= today:
            r = billing.run_billing(d, trigger="demo-replay")
            for inv in Invoice.objects.filter(issue_date=d, state="draft").exclude(kind="credit_note"):
                billing.confirm_invoice(inv)
                self._schedule_payment(events, inv, behaviours.get(inv.subscription_id, "good"), rnd2, today)
            for ev in events.pop(d, []):
                self._apply(ev, d, analytics, sana, rohit)
            d += timedelta(days=1 if d > today - timedelta(days=70) else 3)
        for day in sorted(events):  # events scheduled beyond the loop's step
            if day <= today:
                for ev in events.pop(day):
                    self._apply(ev, day, analytics, sana, rohit)
        # leave a few recent invoices as drafts for the review workflow
        for inv in Invoice.objects.filter(state="confirmed", issue_date__gte=today - timedelta(days=1), amount_paid=0).order_by("-id")[:5]:
            Invoice.objects.filter(pk=inv.pk).update(state="draft", finalized_at=None)
        Subscription.objects.filter(pk__in=[s.pk for s, _ in created]).update()
        self.quotations(custs, sana, admin, t1, t2, t3, starter, business, enterprise, today)
        # a customer-reported payment and a failed payment for the workflow screens
        confirmed = list(Invoice.objects.filter(state="confirmed", amount_paid=0).exclude(kind="credit_note").order_by("-due_date"))
        if confirmed:
            from billing.payments import report_payment
            inv = confirmed[0]
            report_payment(inv, amount=inv.balance, method="upi", reference="UPI-4821993", user=inv.customer.user) if inv.customer.user else None
        for inv in confirmed[1:4]:
            billing.fail_payment(inv, amount=inv.balance, method="razorpay", reason="Card declined by issuer", provider="razorpay", provider_ref=f"pay_demo{inv.pk}", is_test=True)
        Notification.objects.create(text="Demo data loaded. Explore the overview, then run billing.", url="/dashboard/")
        Task.objects.create(title="Call Gupta Hardware about the overdue invoice", due_date=today + timedelta(days=1), assignee=sana, url="/customers/")
        audit.log(admin, "demo.seed", detail=f"{Subscription.objects.count()} subscriptions, {Invoice.objects.count()} invoices")
        from billing.models import BillingRun
        BillingRun.objects.filter(trigger="demo-replay").delete()
        BillingRun.objects.create(as_of=today, trigger="scheduler", created=0, finished_at=timezone.now())
        if include_expansion:
            self._add_expansion(today, sana, rohit)

    def expand_existing(self):
        """Add the extension only to the known 52-contract seed; never run global maintenance here."""
        original_emails = []
        for name, company in ORGS:
            slug = company.split()[0].lower().replace("&", "and")
            original_emails.append("maya@acme.test" if name == "Maya Rao" else f"{name.split()[0].lower()}@{slug}.test")
        customers = list(Customer.objects.filter(email__in=original_emails))
        counts = {email: sum(customer.email == email for customer in customers) for email in original_emails}
        if len(customers) != len(ORGS) or any(counts[email] != 1 for email in original_emails):
            raise CommandError("--expand requires the original 25-customer demo seed; no records were changed.")
        seed_customer_ids = [c.pk for c in customers]
        base_contracts = Subscription.objects.filter(customer_id__in=seed_customer_ids).order_by("pk")
        if base_contracts.count() < 52:
            raise CommandError("--expand requires the original 52 demo contracts; no records were changed.")

        keyed_emails = [_expansion_email(i) for i in range(1, 14)]
        keyed_customers = list(Customer.objects.filter(email__in=keyed_emails))
        keyed_subs = list(Subscription.objects.filter(notes__startswith=EXPANSION_PREFIX))
        if keyed_subs:
            expected_keys = {f"{EXPANSION_PREFIX}org-{org:02d}-contract-{slot}" for org in range(1, 14) for slot in (1, 2)}
            if ({s.notes for s in keyed_subs} == expected_keys and len(keyed_subs) == 26
                    and {c.email for c in keyed_customers} == set(keyed_emails)):
                self.stdout.write(f"Demo expansion already complete: +{len(keyed_subs)} contracts, +{len(keyed_customers)} organizations (no changes).")
                return
            raise CommandError("A partial or conflicting demo expansion exists; no records were changed.")
        if keyed_customers:
            raise CommandError("Expansion email keys already exist without matching contracts; no records were changed.")
        with transaction.atomic():
            before_contracts = Subscription.objects.count()
            before_customers = Customer.objects.count()
            sana = User.objects.get(email="staff@brightloop.test")
            rohit = User.objects.get(email="finance@brightloop.test")
            self._add_expansion(timezone.localdate(), sana, rohit)
            self.stdout.write(self.style.SUCCESS(
                f"Demo expansion added 26 contracts and 13 organizations; totals are "
                f"{Subscription.objects.count()} contracts and {Customer.objects.count()} organizations "
                f"(was {before_contracts} and {before_customers})."))

    def _add_expansion(self, today, sana, rohit):
        """Create additive, keyed demo history through subscription and billing services only."""
        from core.money import q
        from subscriptions.models import Subscription as SubModel

        if Subscription.objects.filter(notes__startswith=EXPANSION_PREFIX).exists():
            return
        starter = Plan.objects.get(name="Starter", interval="monthly")
        weekly = Plan.objects.get(name="Weekly Pilot", interval="weekly")
        daily = Plan.objects.get(name="Daily Trial", interval="daily")
        business = Plan.objects.get(name="Business", interval="monthly")
        annual = Plan.objects.get(name="Team Annual", interval="yearly")
        templates = {t.plan_id: t for t in QuotationTemplate.objects.filter(name__in=("Business + Premium Suite",))}

        org_customers = []
        for i, (name, company, sector, neighborhood) in enumerate(EXPANSION_ORGS, 1):
            city, postcode_prefix = (("Lucknow", "226"), ("Kanpur", "208"), ("Prayagraj", "211"), ("Varanasi", "221"))[(i * 7) % 4]
            area = neighborhood if city == "Lucknow" else ("Civil Lines", "Mall Road", "Sigra", "George Town")[(i * 3) % 4]
            customer = Customer.objects.create(
                name=name, company=company, email=_expansion_email(i),
                phone=f"+91 98{(73100000 + i * 3791):08d}",
                address=f"{12 + i * 3}, {area}, {city}, Uttar Pradesh {postcode_prefix}{(i * 73) % 1000:03d}",
                tax_id=f"09{(i * 271828):09d}F1Z{i % 10}", account_manager=sana if i % 2 else rohit,
                notes=f"{sector} · demo scenario {i:02d}")
            org_customers.append(customer)

        starter_periods = [4] * 12  # 48 periods, all after the expired spring offer
        business_periods = [4] * 4  # 16 periods; seats stay below the volume-discount threshold
        active_specs = ([ (starter, n, 1 + i % 4, "net15") for i, n in enumerate(starter_periods) ]
                        + [ (business, n, 3 + i, "net30") for i, n in enumerate(business_periods) ]
                        + [ (weekly, n, 1 + i % 3, "immediate") for i, n in enumerate((6, 6, 6, 8)) ]
                        + [ (daily, n, 1 + i % 3, "immediate") for i, n in enumerate((7, 7, 7, 7)) ])
        if len(active_specs) != 24 or sum(n for _, n, _, _ in active_specs) != 118:
            raise CommandError("Internal demo expansion scenario count is inconsistent.")

        active_subs = []
        for i, (plan, period_count, quantity, terms) in enumerate(active_specs):
            customer = org_customers[i // 2]
            interval = plan.interval
            start = add_interval(today, interval, -(period_count - 1))
            start -= timedelta(days=(i % 20) if interval == "monthly" else 0)
            notes = f"{EXPANSION_PREFIX}org-{i // 2 + 1:02d}-contract-{i % 2 + 1}"
            sub = subs.create_subscription(customer=customer, plan=plan, start_date=start, user=sana if i % 2 else rohit,
                                           quantity=quantity, payment_terms=terms, notes=notes)
            subs.send_quotation(sub, actor=sana)
            sub = subs.accept(Subscription.objects.get(pk=sub.pk), actor=sana, signed_name=customer.name)
            if sub.state == SubModel.CONFIRMED:
                sub = subs.activate(sub, effective=start, actor=sana)
            active_subs.append((sub, period_count))

        # Two deliberately open quotations round out lifecycle coverage without inventing cash.
        for slot, plan in ((1, business), (2, annual)):
            org_index = 12
            customer = org_customers[org_index]
            template = templates.get(plan.pk)
            sub = subs.create_subscription(customer=customer, plan=plan, template=template, user=sana,
                                           quantity=max(plan.min_quantity, 3 if plan == business else 1),
                                           notes=f"{EXPANSION_PREFIX}org-13-contract-{slot}")
            subs.send_quotation(sub, actor=sana)

        expansion_invoice_ids = []
        for sub, count in active_subs:
            for index in range(count):
                start, _ = billing.period_bounds(sub, index)
                inv = billing.create_invoice_for_period(sub, index, start)
                billing.confirm_invoice(inv)
                expansion_invoice_ids.append(inv.pk)
            sub.periods_billed = count
            sub.next_invoice_date = add_interval(sub.start_date, sub.plan.interval, count)
            sub.save(update_fields=["periods_billed", "next_invoice_date"])

        invoices = list(Invoice.objects.filter(pk__in=expansion_invoice_ids).select_related("subscription").order_by("issue_date", "pk"))
        by_oldest = sorted(invoices, key=lambda inv: (inv.issue_date, inv.pk))
        full_paid, late_paid, partial, failed = by_oldest[:80], by_oldest[80:90], by_oldest[90:95], by_oldest[95:105]
        # All cash dates are bounded by the invoice date and today; late items are selected from oldest due dates.
        for inv in full_paid:
            pay_date = min(today, inv.issue_date + timedelta(days=2))
            billing.record_payment(inv, amount=inv.balance, method=("bank", "upi", "card", "cheque")[inv.pk % 4], date=pay_date,
                                   user=rohit, reference=f"DEMO-EXP-{inv.pk}", idempotency_key=f"{EXPANSION_PREFIX}pay-{inv.pk}")
        for inv in late_paid:
            pay_date = min(today, max(inv.issue_date, inv.due_date + timedelta(days=3)))
            billing.record_payment(inv, amount=inv.balance, method="bank", date=pay_date, user=rohit,
                                   reference=f"DEMO-EXP-LATE-{inv.pk}", idempotency_key=f"{EXPANSION_PREFIX}late-{inv.pk}")
        for inv in partial:
            first_date = min(today, inv.issue_date + timedelta(days=2))
            amount = q(inv.total / Decimal("2"))
            billing.record_payment(inv, amount=amount, method="upi", date=first_date, user=rohit,
                                   reference=f"DEMO-EXP-PART-A-{inv.pk}", idempotency_key=f"{EXPANSION_PREFIX}partial-a-{inv.pk}")
            second_date = min(today, max(first_date, inv.issue_date + timedelta(days=10)))
            current = Invoice.objects.get(pk=inv.pk)
            billing.record_payment(current, amount=current.balance, method="bank", date=second_date, user=rohit,
                                   reference=f"DEMO-EXP-PART-B-{inv.pk}", idempotency_key=f"{EXPANSION_PREFIX}partial-b-{inv.pk}")
        for inv in failed:
            billing.fail_payment(inv, amount=inv.balance, method="razorpay", reason="Card declined by issuer",
                                 provider="razorpay", provider_ref=f"demo-exp-{inv.pk}", is_test=True)

        # One real mid-period add-on amendment and its paid adjustment, scoped to a new contract.
        amend_sub = active_subs[0][0]
        analytics = Product.objects.get(sku="BL-ANALYTICS")
        change = subs.apply_change(amend_sub, addons={analytics.pk: True}, effective="now", today=today, actor=sana)
        adjustment = change["document"]
        if adjustment is None:
            raise CommandError("Expected an adjustment invoice for the demo add-on amendment.")
        billing.confirm_invoice(adjustment)
        billing.record_payment(adjustment, amount=adjustment.total, method="upi", date=today, user=rohit,
                               reference=f"DEMO-EXP-ADJ-{adjustment.pk}", idempotency_key=f"{EXPANSION_PREFIX}adjustment-{adjustment.pk}")

        # The partial credit is refunded for exactly the same cash amount, after the credit note is issued.
        credit_source = Invoice.objects.get(pk=full_paid[0].pk)
        credit_amount = q(credit_source.total / Decimal("2"))
        credit = billing.issue_credit_note(credit_source, amount=credit_amount, reason="Approved service credit", actor=sana)
        billing.refund_payment(credit_source, amount=credit_amount, method="bank", date=max(today, credit.issue_date),
                               note=f"Refund for {credit.number}", user=rohit,
                               idempotency_key=f"{EXPANSION_PREFIX}refund-{credit_source.pk}")

    def _schedule_payment(self, events, inv, beh, rnd, today):
        due = inv.due_date
        issue = inv.issue_date
        if beh == "bad" and issue < today - timedelta(days=25):
            if rnd.random() < 0.75:
                return
        if beh == "late":
            when = due + timedelta(days=rnd.randint(2, 18))
        elif beh == "partial" and rnd.random() < 0.5:
            events[issue + timedelta(days=4)].append(("partial", inv.pk, 0.5))
            events[issue + timedelta(days=24)].append(("pay", inv.pk, 1))
            return
        else:
            when = issue + timedelta(days=rnd.randint(0, min(12, max((due - issue).days, 1))))
        if when <= today and not (beh == "bad" and issue > today - timedelta(days=25)):
            events[when].append(("pay", inv.pk, 1))
        elif beh != "bad" and rnd.random() < 0.5:
            pass

    def _apply(self, ev, day, analytics, sana, rohit):
        kind, pk, arg = ev
        if kind in ("pay", "partial"):
            inv = Invoice.objects.get(pk=pk)
            if inv.state != "confirmed":
                return
            amount = inv.balance if kind == "pay" else (inv.balance * Decimal(str(arg))).quantize(Decimal("0.01"))
            method = ["bank", "upi", "card", "cheque"][pk % 4]
            billing.record_payment(inv, amount=amount, method=method, date=day, reference=f"{method.upper()}-{100000 + pk * 37}", user=rohit, idempotency_key=f"demo-{kind}-{pk}")
            if pk % 17 == 0 and kind == "pay":
                inv.refresh_from_db()
                credit_amount = min(Decimal("250.00"), inv.total / 2)
                cn = billing.issue_credit_note(inv, amount=credit_amount, reason="Goodwill credit", actor=sana)
                billing.refund_payment(inv, amount=min(credit_amount, inv.amount_paid), method=method,
                                       date=max(day, cn.issue_date), note="Goodwill refund", user=rohit)
            return
        s = Subscription.objects.select_related("plan", "customer").get(pk=pk)
        if s.state != "active":
            return
        try:
            if kind == "seats":
                subs.apply_change(s, quantity=arg, effective="now", today=day, actor=sana)
            elif kind == "addon":
                subs.apply_change(s, addons={arg: True}, effective="now", today=day, actor=sana)
            elif kind == "downgrade":
                pl = s.lines.filter(is_plan_line=True).first()
                if pl.quantity > s.plan.min_quantity:
                    subs.apply_change(s, quantity=pl.quantity - 1, effective="now", today=day, actor=sana)
            elif kind == "close":
                s.close_reason = "too_expensive"
                subs._finish_close(s, actor=sana, effective=day)
        except subs.TransitionError:
            pass

    def quotations(self, custs, sana, admin, t1, t2, t3, starter, business, enterprise, today):
        # draft, sent, expired, rejected, alternatives, upsell, renewal quotation
        q1 = subs.create_subscription(customer=custs[1], plan=business, template=t1, user=sana, quantity=6)
        subs.send_quotation(q1)
        subs.create_subscription(customer=custs[2], plan=starter, template=t2, user=sana)
        q3 = subs.create_subscription(customer=custs[3], plan=enterprise, template=t3, user=admin)
        subs.send_quotation(q3)
        alt = subs.clone_as_quote(q3, user=admin)
        subs.send_quotation(alt)
        q5 = subs.create_subscription(customer=custs[16], plan=starter, template=t2, user=sana)
        subs.send_quotation(q5)
        subs.reject(q5, actor=sana, reason="Went with another vendor")
        q6 = subs.create_subscription(customer=custs[17], plan=starter, template=t2, user=sana)
        subs.send_quotation(q6)
        Subscription.objects.filter(pk=q6.pk).update(quote_valid_until=today - timedelta(days=4))
        active = Subscription.objects.filter(state="active", plan__interval="monthly", renewals__isnull=True).order_by("id").first()
        if active:
            subs.clone_as_quote(active, user=sana, kind="upsell")
        soon = Subscription.objects.filter(state="active", expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30), renewals__isnull=True, plan__allow_renew=True).order_by("id").first()
        if soon:
            subs.renew(soon, actor=sana, accept=False)

    # -------------------------------------------------------------- performance dataset
    @transaction.atomic
    def bulk(self, n):
        """Direct inserts: N active subscriptions with their change history, 1-6 invoices each and mostly settled payments."""
        rnd = random.Random(42)
        today = timezone.localdate()
        plans = list(Plan.objects.filter(interval__in=["monthly", "yearly"]))
        if not plans:
            raise CommandError("Run seed_demo first so plans and taxes exist.")
        tax = Tax.default()
        base = Customer.objects.count()
        Customer.objects.bulk_create([Customer(name=f"Bulk Customer {base + i}", email=f"bulk{base + i}@example.test", company=f"Co {base + i}") for i in range(n)])
        customers = list(Customer.objects.filter(email__startswith="bulk").order_by("-id")[:n])
        subs_ = []
        for i, c in enumerate(customers):
            plan = rnd.choice(plans)
            k = rnd.randint(1, 6)
            start = add_interval(today, plan.interval, -k) + timedelta(days=rnd.randint(0, 3))
            subs_.append(Subscription(number=f"BLK{base + i:07d}", customer=c, plan=plan, state="active", start_date=start, expiry_date=add_interval(start, "yearly", 1), payment_terms="net15",
                                      periods_billed=k, next_invoice_date=add_interval(start, plan.interval, k), activated_at=timezone.now()))
        Subscription.objects.bulk_create(subs_, batch_size=500)
        subs_ = list(Subscription.objects.filter(number__startswith="BLK").order_by("-id")[:n])
        SubscriptionLine.objects.bulk_create([SubscriptionLine(subscription=s, label=s.plan.name, quantity=rnd.randint(1, 20), unit_price=s.plan.price, tax=tax, is_plan_line=True) for s in subs_], batch_size=500)
        lines = {l.subscription_id: l for l in SubscriptionLine.objects.filter(subscription__in=subs_)}
        SubscriptionChange.objects.bulk_create([SubscriptionChange(
            subscription=s, kind="activated", effective_date=s.start_date, mrr_before=0, mrr_after=lines[s.pk].net_amount * Plan.MONTHLY_FACTOR[s.plan.interval], movement="new") for s in subs_], batch_size=500)
        invoices, meta = [], []
        for s in subs_:
            l = lines[s.pk]
            net, tx = l.net_amount, l.tax_amount
            for p in range(s.periods_billed):
                ps = add_interval(s.start_date, s.plan.interval, p)
                paid = rnd.random() < 0.7
                invoices.append(Invoice(number=f"INV/B/{s.pk}-{p}", subscription=s, customer=s.customer, state="paid" if paid else "confirmed", period_start=ps,
                                        period_end=add_interval(s.start_date, s.plan.interval, p + 1) - timedelta(days=1), issue_date=ps, due_date=ps + timedelta(days=15),
                                        bill_to_name=s.customer.display_name, bill_to_email=s.customer.email, subtotal=net, tax_total=tx, total=net + tx,
                                        amount_paid=net + tx if paid else 0, finalized_at=timezone.now()))
                meta.append((s, p, l, net, tx, paid))
        Invoice.objects.bulk_create(invoices, batch_size=500)
        created = {i.number: i for i in Invoice.objects.filter(number__startswith="INV/B/")}
        il, pay, per = [], [], []
        for inv, (s, p, l, net, tx, paid) in zip(invoices, meta):
            real = created[inv.number]
            il.append(InvoiceLine(invoice=real, description=l.label, quantity=l.quantity, unit_price=l.unit_price, tax_name="GST", tax_rate=18, tax_amount=tx, amount=net))
            per.append(BillingPeriod(subscription=s, index=p, start=real.period_start, end=real.period_end, invoice=real))
            if paid:
                pay.append(Payment(invoice=real, amount=net + tx, method=rnd.choice(["bank", "upi", "card"]), date=min(today, real.issue_date + timedelta(days=rnd.randint(1, 20)))))
        InvoiceLine.objects.bulk_create(il, batch_size=500)
        BillingPeriod.objects.bulk_create(per, batch_size=500)
        Payment.objects.bulk_create(pay, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Added {n} subscriptions, {len(invoices)} invoices, {len(pay)} payments."))
