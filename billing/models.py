import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import connection, models, transaction
from django.db.models import Q
from django.utils import timezone

from core.money import ZERO


class Invoice(models.Model):
    DRAFT, CONFIRMED, PAID, CANCELLED = "draft", "confirmed", "paid", "cancelled"
    STATES = [(DRAFT, "Draft"), (CONFIRMED, "Confirmed"), (PAID, "Paid"), (CANCELLED, "Cancelled")]
    RECURRING, ADJUSTMENT, CREDIT_NOTE, ONE_OFF = "recurring", "adjustment", "credit_note", "one_off"
    KINDS = [(RECURRING, "Recurring"), (ADJUSTMENT, "Adjustment"), (CREDIT_NOTE, "Credit note"), (ONE_OFF, "One-off")]
    SNAPSHOT_FIELDS = ("number", "kind", "subscription_id", "customer_id", "period_start", "period_end", "issue_date", "due_date",
                       "bill_to_name", "bill_to_email", "bill_to_address", "bill_to_tax_id", "subtotal", "discount_total", "tax_total", "total",
                       "discount_label", "replaces_id", "credit_for_id")

    number = models.CharField(max_length=24, unique=True, editable=False)
    kind = models.CharField(max_length=12, default=RECURRING, choices=KINDS, db_index=True)
    subscription = models.ForeignKey("subscriptions.Subscription", on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="invoices")
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT, db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(db_index=True)
    # Snapshots: later catalogue/customer edits never rewrite a past bill
    bill_to_name = models.CharField(max_length=200)
    bill_to_email = models.EmailField()
    bill_to_address = models.TextField(blank=True)
    bill_to_tax_id = models.CharField(max_length=40, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credited_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_label = models.CharField(max_length=160, blank=True)
    replaces = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replacement")
    credit_for = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_notes")
    finalized_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveIntegerField(default=0)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "period_start"], condition=Q(replaces__isnull=True, kind="recurring"),
                                    name="uniq_invoice_per_period"),
        ]
        indexes = [models.Index(fields=["state", "due_date"]), models.Index(fields=["customer", "state"]),
                   models.Index(fields=["issue_date"]), models.Index(fields=["kind", "state"])]

    def __str__(self):
        return self.number

    @transaction.atomic
    def save(self, *a, **kw):
        creating = not self.pk
        if not creating:
            qs = Invoice.objects.filter(pk=self.pk)
            if connection.features.has_select_for_update:
                qs = qs.select_for_update()
            old = qs.values(*self.SNAPSHOT_FIELDS, "finalized_at").first()
            if old and old["finalized_at"] and (old["finalized_at"] != self.finalized_at or any(old[f] != getattr(self, f) for f in self.SNAPSHOT_FIELDS)):
                raise ValidationError("A finalized invoice cannot be edited. Issue a credit note or replacement instead.")
        if creating:
            self.number = f"TMP-{uuid.uuid4().hex[:14]}"
        super().save(*a, **kw)
        if creating:
            prefix = "CN" if self.kind == self.CREDIT_NOTE else "INV"
            self.number = f"{prefix}/{self.issue_date.year}/{self.pk:05d}"
            super().save(update_fields=["number"])

    @property
    def is_credit_note(self):
        return self.kind == self.CREDIT_NOTE

    @property
    def balance(self):
        if self.state == self.CANCELLED or self.is_credit_note:
            return ZERO
        return self.total - self.credited_total - self.amount_paid

    @property
    def is_overdue(self):
        return self.state == self.CONFIRMED and self.balance > 0 and self.due_date < timezone.localdate()

    @property
    def display_state(self):
        if self.is_overdue:
            return "overdue"
        if self.state == self.CONFIRMED and self.amount_paid > 0:
            return "partial"
        return self.state

    @property
    def days_overdue(self):
        return (timezone.localdate() - self.due_date).days if self.is_overdue else 0

    @property
    def is_locked(self):
        return self.finalized_at is not None

    @property
    def fingerprint(self):
        """Short integrity code printed on the document: changes if any legally relevant figure or party changes."""
        import hashlib
        parts = [self.number, str(self.issue_date), self.bill_to_name, str(self.total), str(self.tax_total)] + [f"{l.description}|{l.quantity}|{l.amount}" for l in self.lines.all()]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16].upper()

    @property
    def tax_summary(self):
        """Taxable value and tax per rate, for the legal tax breakdown on the document."""
        rows = {}
        for l in self.lines.all():
            r = rows.setdefault(l.tax_rate, {"rate": l.tax_rate, "taxable": ZERO, "tax": ZERO, "name": l.tax_name})
            r["taxable"] += l.amount
            r["tax"] += l.tax_amount
        return sorted(rows.values(), key=lambda r: r["rate"])


class InvoiceLine(models.Model):
    # shortcut: bulk ORM writes bypass these guards; use database enforcement if untrusted bulk writers are introduced.
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=240)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_name = models.CharField(max_length=80, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField("Amount (pre-tax)", max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    @transaction.atomic
    def save(self, *args, **kwargs):
        old_invoice_id = InvoiceLine.objects.filter(pk=self.pk).values_list("invoice_id", flat=True).first() if self.pk else None
        invoice_ids = sorted({invoice_id for invoice_id in (old_invoice_id, self.invoice_id) if invoice_id})
        invoices = Invoice.objects.filter(pk__in=invoice_ids).order_by("pk")
        if connection.features.has_select_for_update:
            invoices = invoices.select_for_update()
        locked = {invoice.pk: invoice for invoice in invoices}
        if any(locked[invoice_id].finalized_at for invoice_id in invoice_ids if invoice_id in locked):
            raise ValidationError("A finalized invoice line cannot be edited, added, or moved.")
        return super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        invoice = Invoice.objects.filter(pk=self.invoice_id)
        if connection.features.has_select_for_update:
            invoice = invoice.select_for_update()
        if invoice.filter(finalized_at__isnull=False).exists():
            raise ValidationError("A finalized invoice line cannot be removed.")
        return super().delete(*args, **kwargs)

    @property
    def gross(self):
        return self.unit_price * self.quantity


class BillingPeriod(models.Model):
    """One covered period of a subscription. The unique key is what makes billing idempotent at the database level."""

    subscription = models.ForeignKey("subscriptions.Subscription", on_delete=models.CASCADE, related_name="periods")
    index = models.PositiveIntegerField()
    start = models.DateField()
    end = models.DateField()
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="periods")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["subscription_id", "index"]
        constraints = [models.UniqueConstraint(fields=["subscription", "index"], name="uniq_billing_period")]


class DiscountUse(models.Model):
    """Ties one use of a discount rule to one invoice. Unique so a retry can never consume it twice."""

    discount = models.ForeignKey("catalog.Discount", on_delete=models.PROTECT, related_name="uses")
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="discount_use")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    released = models.BooleanField(default=False)  # set when the invoice is cancelled; the use is given back


class Payment(models.Model):
    METHODS = [("bank", "Bank transfer"), ("upi", "UPI"), ("card", "Card"), ("cash", "Cash"), ("cheque", "Cheque"),
               ("razorpay", "Online (Razorpay)"), ("other", "Other")]
    SUCCEEDED, PENDING, FAILED = "succeeded", "pending", "failed"
    STATUSES = [(SUCCEEDED, "Received"), (PENDING, "Awaiting confirmation"), (FAILED, "Failed")]

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    status = models.CharField(max_length=10, choices=STATUSES, default=SUCCEEDED, db_index=True)
    method = models.CharField(max_length=10, choices=METHODS, default="bank")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.localdate, db_index=True)
    reference = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=240, blank=True)
    provider = models.CharField(max_length=20, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    is_test = models.BooleanField(default=False, help_text="Sandbox money. Excluded from cash-received reports.")
    reconciled = models.BooleanField(default=True)
    failure_reason = models.CharField(max_length=240, blank=True)
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["provider", "provider_ref"], condition=~Q(provider=""), name="uniq_provider_payment")]
        indexes = [models.Index(fields=["status", "date"]), models.Index(fields=["is_test", "status", "date"])]

    @property
    def counts(self):
        return self.status == self.SUCCEEDED


class PaymentLink(models.Model):
    """Hosted checkout link created at the payment provider for one invoice balance."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payment_links")
    provider = models.CharField(max_length=20)
    provider_id = models.CharField(max_length=80)
    url = models.URLField(max_length=400)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, default="created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["provider", "provider_id"], name="uniq_payment_link")]


class PaymentEvent(models.Model):
    """Inbound provider event. Unique (provider, event_id) gives webhook deduplication."""

    provider = models.CharField(max_length=20)
    event_id = models.CharField(max_length=120)
    kind = models.CharField(max_length=60)
    outcome = models.CharField(max_length=200, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="uniq_provider_event")]


class BillingRun(models.Model):
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=40, default="scheduler")
    as_of = models.DateField()
    created = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    closed = models.PositiveIntegerField(default=0)
    renewed = models.PositiveIntegerField(default=0)
    errors = models.TextField(blank=True)

    class Meta:
        ordering = ["-id"]


class BillingFailure(models.Model):
    """A subscription the billing run could not process. Cleared when a later run succeeds for it."""

    subscription = models.ForeignKey("subscriptions.Subscription", on_delete=models.CASCADE, related_name="billing_failures")
    run = models.ForeignKey(BillingRun, on_delete=models.CASCADE, related_name="failures")
    message = models.CharField(max_length=500)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
