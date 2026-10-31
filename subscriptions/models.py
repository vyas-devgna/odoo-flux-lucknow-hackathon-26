import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from catalog.models import Plan, Product, ProductVariant, QuotationTemplate, Tax
from core.money import ZERO, q

TERMS = [("immediate", "Immediate", 0), ("net15", "Net 15", 15), ("net30", "Net 30", 30), ("net60", "Net 60", 60)]
TERM_DAYS = {k: d for k, _, d in TERMS}


def add_interval(anchor, interval, n):
    """anchor + n billing intervals. Month/year steps keep the anchor's day, clamped to month end
    (31 Jan -> 28 Feb -> 31 Mar), because every step is computed from the original anchor."""
    if interval == Plan.DAILY:
        return anchor + timedelta(days=n)
    if interval == Plan.WEEKLY:
        return anchor + timedelta(weeks=n)
    months = n if interval == Plan.MONTHLY else 12 * n
    idx = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(idx, 12)
    month += 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


class Subscription(models.Model):
    DRAFT, QUOTATION, CONFIRMED, ACTIVE, PAUSED, CLOSED = "draft", "quotation", "confirmed", "active", "paused", "closed"
    STATES = [(DRAFT, "Draft"), (QUOTATION, "Quotation"), (CONFIRMED, "Confirmed"), (ACTIVE, "Active"),
              (PAUSED, "Paused"), (CLOSED, "Closed")]
    FLOW = [DRAFT, QUOTATION, CONFIRMED, ACTIVE, CLOSED]

    number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    template = models.ForeignKey(QuotationTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT, db_index=True)
    start_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=10, choices=[(k, l) for k, l, _ in TERMS], default="net15")
    quote_valid_until = models.DateField(null=True, blank=True)
    periods_billed = models.PositiveIntegerField(default=0, editable=False)
    next_invoice_date = models.DateField(null=True, blank=True, db_index=True, editable=False)
    renewed_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["state", "next_invoice_date"])]

    def __str__(self):
        return self.number

    def save(self, *a, **kw):
        creating = not self.pk
        if creating:
            self.number = f"TMP-{uuid.uuid4().hex[:12]}"
        super().save(*a, **kw)
        if creating:
            self.number = f"SUB{self.pk:05d}"
            super().save(update_fields=["number"])

    # ---- money (computed from lines; invoices snapshot their own) ----
    @property
    def subtotal(self):
        return sum((l.amount for l in self.lines.all()), ZERO)

    @property
    def tax_total(self):
        return sum((l.tax_amount for l in self.lines.all()), ZERO)

    @property
    def total(self):
        return self.subtotal + self.tax_total

    @property
    def recurring_monthly(self):
        return q(self.subtotal * Plan.MONTHLY_FACTOR[self.plan.interval])

    @property
    def payment_term_days(self):
        return TERM_DAYS[self.payment_terms]

    # ---- state helpers ----
    @property
    def is_editable(self):
        return self.state in (self.DRAFT, self.QUOTATION)

    @property
    def quote_expired(self):
        return bool(self.state in (self.DRAFT, self.QUOTATION) and self.quote_valid_until
                    and self.quote_valid_until < timezone.localdate())

    @property
    def stage_index(self):
        return self.FLOW.index(self.ACTIVE if self.state == self.PAUSED else self.state)

    def can(self, action):
        s, p = self.state, self.plan
        return {
            "send": s == self.DRAFT,
            "confirm": s == self.QUOTATION,
            "activate": s == self.CONFIRMED,
            "pause": s == self.ACTIVE and p.allow_pause,
            "resume": s == self.PAUSED,
            "close": s in (self.ACTIVE, self.PAUSED) and p.allow_early_close,
            "renew": s in (self.ACTIVE, self.CLOSED) and p.allow_renew and not self.renewals.exists(),
            "addons": s in (self.ACTIVE, self.CONFIRMED, self.PAUSED),
        }.get(action, False)


class SubscriptionLine(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    label = models.CharField("Description", max_length=200, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["id"]

    @property
    def description(self):
        if self.label:
            return self.label
        name = self.product.name if self.product_id else "Item"
        return f"{name} ({self.variant.value})" if self.variant_id else name

    @property
    def amount(self):
        return q(self.unit_price * self.quantity)

    @property
    def tax_rate(self):
        return self.tax.rate if self.tax_id else Decimal("0")

    @property
    def tax_amount(self):
        return q(self.amount * self.tax_rate / 100)

    @property
    def is_addon(self):
        return bool(self.product_id and self.product.is_addon)
