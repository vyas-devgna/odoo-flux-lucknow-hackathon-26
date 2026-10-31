import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.money import ZERO


class Invoice(models.Model):
    DRAFT, CONFIRMED, PAID, CANCELLED = "draft", "confirmed", "paid", "cancelled"
    STATES = [(DRAFT, "Draft"), (CONFIRMED, "Confirmed"), (PAID, "Paid"), (CANCELLED, "Cancelled")]

    number = models.CharField(max_length=24, unique=True, editable=False)
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
    discount_label = models.CharField(max_length=160, blank=True)
    replaces = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replacement")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # One ordinary invoice per subscription period; an explicit replacement is the only exception.
            models.UniqueConstraint(fields=["subscription", "period_start"], condition=Q(replaces__isnull=True),
                                    name="uniq_invoice_per_period"),
        ]
        indexes = [models.Index(fields=["state", "due_date"]), models.Index(fields=["customer", "state"])]

    def __str__(self):
        return self.number

    def save(self, *a, **kw):
        creating = not self.pk
        if creating:
            self.number = f"TMP-{uuid.uuid4().hex[:14]}"
        super().save(*a, **kw)
        if creating:
            self.number = f"INV/{self.issue_date.year}/{self.pk:05d}"
            super().save(update_fields=["number"])

    @property
    def balance(self):
        return ZERO if self.state == self.CANCELLED else self.total - self.amount_paid

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


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
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

    @property
    def gross(self):
        return self.unit_price * self.quantity


class DiscountUse(models.Model):
    """Ties one use of a discount rule to one invoice. Unique so a retry can never consume it twice."""

    discount = models.ForeignKey("catalog.Discount", on_delete=models.PROTECT, related_name="uses")
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="discount_use")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    released = models.BooleanField(default=False)  # set when the invoice is cancelled; the use is given back


class Payment(models.Model):
    METHODS = [("bank", "Bank transfer"), ("upi", "UPI"), ("card", "Card"), ("cash", "Cash"), ("cheque", "Cheque"), ("other", "Other")]

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=METHODS, default="bank")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    date = models.DateField(default=timezone.localdate, db_index=True)
    note = models.CharField(max_length=240, blank=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]


class BillingRun(models.Model):
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=20, default="scheduler")
    as_of = models.DateField()
    created = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    closed = models.PositiveIntegerField(default=0)
    errors = models.TextField(blank=True)

    class Meta:
        ordering = ["-id"]
