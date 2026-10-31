from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """Append-only trail. Written by core.audit.log(); never edited through the UI."""

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]


class CompanySettings(models.Model):
    """Singleton (pk=1)."""

    name = models.CharField("Company name", max_length=160, default="Brightloop Studio")
    address = models.TextField(blank=True)
    tax_id = models.CharField("Tax ID", max_length=40, blank=True)
    email = models.EmailField("Billing email", blank=True)
    phone = models.CharField(max_length=30, blank=True)
    bank_details = models.TextField("Payment instructions", blank=True, help_text="Shown on invoices, e.g. bank account or UPI ID.")
    invoice_footer = models.CharField(max_length=300, blank=True, default="Thank you for your business.")
    default_payment_terms = models.CharField(max_length=10, default="net15", choices=[("immediate", "Immediate"), ("net15", "Net 15"), ("net30", "Net 30"), ("net60", "Net 60")])
    auto_confirm_invoices = models.BooleanField("Confirm invoices automatically", default=False, help_text="Billing runs create invoices already confirmed instead of drafts.")
    auto_email_invoices = models.BooleanField("Email invoices automatically", default=False, help_text="Implies confirmation. Customers receive each invoice the moment it is created.")
    reminder_days_before_renewal = models.PositiveIntegerField("Renewal notice (days before expiry)", default=14)
    reminder_repeat_days = models.PositiveIntegerField("Repeat overdue reminders every (days)", default=7)
    payment_exception_days = models.PositiveIntegerField("Flag a subscription when an invoice is this many days overdue", default=14)

    class Meta:
        verbose_name_plural = "company settings"

    def save(self, *a, **kw):
        self.pk = 1
        super().save(*a, **kw)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


class RateLimitHit(models.Model):
    """Generic sliding-window counter for sensitive endpoints (login, reset, signup)."""

    scope = models.CharField(max_length=30, db_index=True)
    key = models.CharField(max_length=254, db_index=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    @classmethod
    def count(cls, scope, key, minutes):
        return cls.objects.filter(scope=scope, key=key, at__gte=timezone.now() - timezone.timedelta(minutes=minutes)).count()

    @classmethod
    def hit(cls, scope, key):
        cls.objects.create(scope=scope, key=key)
        if cls.objects.count() > 20000:  # housekeeping
            cls.objects.filter(at__lt=timezone.now() - timezone.timedelta(days=1)).delete()


class Outbox(models.Model):
    """Transactional email outbox: rows are written inside the business transaction and delivered afterwards,
    with retries. The app stays usable when the mail server is down."""

    PENDING, SENT, FAILED = "pending", "sent", "failed"
    to = models.CharField(max_length=254)
    subject = models.CharField(max_length=250)
    body = models.TextField()
    reply_to = models.CharField(max_length=254, blank=True)
    status = models.CharField(max_length=8, default=PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    dedupe_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    related_type = models.CharField(max_length=40, blank=True)
    related_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class EmailTemplate(models.Model):
    key = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    subject = models.CharField(max_length=200)
    body = models.TextField(help_text="Use {customer}, {number}, {amount}, {due_date}, {plan}, {company}, {link}.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AutomationRule(models.Model):
    TRIGGERS = [("subscription_activated", "Subscription activated"), ("subscription_paused", "Subscription paused"),
                ("subscription_closed", "Subscription closed"), ("plan_changed", "Plan or seats changed"),
                ("renewal_upcoming", "Renewal coming up"), ("invoice_due_soon", "Invoice due soon"), ("invoice_overdue", "Invoice overdue"),
                ("payment_failed", "Payment failed"), ("quotation_expiring", "Quotation about to expire")]
    ACTIONS = [("email_customer", "Email the customer"), ("notify_staff", "Notify staff"), ("create_task", "Create a follow-up task"),
               ("log", "Record in activity only")]
    name = models.CharField(max_length=120)
    trigger = models.CharField(max_length=30, choices=TRIGGERS)
    action = models.CharField(max_length=20, choices=ACTIONS, default="email_customer")
    template = models.ForeignKey(EmailTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    offset_days = models.PositiveIntegerField("Days", default=0, help_text="For scheduled triggers: how many days before (renewal, due, expiry) or after (overdue) the date.")
    repeat_days = models.PositiveIntegerField("Repeat every (days)", default=0, help_text="0 sends once.")
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["trigger", "id"]

    def __str__(self):
        return self.name


class AutomationLog(models.Model):
    """One execution of a rule against one record; unique key stops duplicates on re-runs."""

    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name="logs")
    target_type = models.CharField(max_length=40)
    target_id = models.PositiveBigIntegerField()
    bucket = models.CharField(max_length=20, default="once")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rule", "target_type", "target_id", "bucket"], name="uniq_automation_run")]


class Notification(models.Model):
    """In-app notification for staff. user=null means every internal user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=240)
    url = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_by = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")

    class Meta:
        ordering = ["-id"]


class Task(models.Model):
    title = models.CharField(max_length=200)
    due_date = models.DateField(null=True, blank=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    url = models.CharField(max_length=200, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["done_at", "due_date", "-id"]
