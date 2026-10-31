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
    DRAFT, QUOTATION, CONFIRMED, ACTIVE, PAUSED, CLOSED, CANCELLED = (
        "draft", "quotation", "confirmed", "active", "paused", "closed", "cancelled")
    STATES = [(DRAFT, "Draft"), (QUOTATION, "Quotation"), (CONFIRMED, "Confirmed"), (ACTIVE, "Active"),
              (PAUSED, "Paused"), (CLOSED, "Closed"), (CANCELLED, "Cancelled")]
    FLOW = [DRAFT, QUOTATION, CONFIRMED, ACTIVE, CLOSED]
    CLOSE_REASONS = [("customer_request", "Customer request"), ("too_expensive", "Too expensive"), ("missing_features", "Missing features"),
                     ("switched", "Switched provider"), ("no_longer_needed", "No longer needed"), ("non_payment", "Non-payment"),
                     ("expired", "Contract ended"), ("other", "Other")]

    number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    template = models.ForeignKey(QuotationTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    salesperson = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="sold_subscriptions")
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT, db_index=True)
    start_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=10, choices=[(k, l) for k, l, _ in TERMS], default="net15")
    renewal_policy = models.CharField(max_length=6, choices=[("auto", "Renews automatically"), ("manual", "Renewal needs approval")], default="manual")
    quote_valid_until = models.DateField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    periods_billed = models.PositiveIntegerField(default=0, editable=False)
    next_invoice_date = models.DateField(null=True, blank=True, db_index=True, editable=False)
    renewed_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals")
    alternative_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="alternatives")
    pending_cancel_at = models.DateField(null=True, blank=True, editable=False)
    close_reason = models.CharField(max_length=20, choices=CLOSE_REASONS, blank=True)
    close_note = models.CharField(max_length=300, blank=True)
    payment_exception = models.BooleanField(default=False, editable=False)
    renewal_notice_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["state", "next_invoice_date"]), models.Index(fields=["customer", "state"]),
                   models.Index(fields=["state", "expiry_date"])]

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
    def _lines(self):
        cache = getattr(self, "_prefetched_objects_cache", {})
        return cache["lines"] if "lines" in cache else list(self.lines.select_related("tax", "product", "variant"))

    @property
    def subtotal(self):
        return sum((l.net_amount for l in self._lines()), ZERO)

    @property
    def tax_total(self):
        return sum((l.tax_amount for l in self._lines()), ZERO)

    @property
    def total(self):
        return self.subtotal + self.tax_total

    @property
    def recurring_monthly(self):
        """MRR contribution: recurring lines only, normalised to a month."""
        base = sum((l.net_amount for l in self._lines() if l.is_recurring), ZERO)
        return q(base * Plan.MONTHLY_FACTOR[self.plan.interval])

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
        s = self.state
        if s == self.PAUSED:
            s = self.ACTIVE
        if s == self.CANCELLED:
            return 0
        return self.FLOW.index(s)

    @property
    def in_minimum_term(self):
        n = self.plan.min_duration_periods
        return bool(n and self.state in (self.ACTIVE, self.PAUSED) and
                    timezone.localdate() < add_interval(self.start_date, self.plan.interval, n))

    BUTTONS = [("send", "Send quotation"), ("confirm", "Confirm"), ("activate", "Activate"), ("pause", "Pause"),
               ("resume", "Resume"), ("renew", "Renew"), ("close", "Close")]

    @property
    def action_buttons(self):
        return [(a, l) for a, l in self.BUTTONS if self.can(a)]

    @property
    def portal_buttons(self):
        labels = {"confirm": "Accept quotation", "pause": "Pause", "resume": "Resume", "renew": "Renew"}
        return [(a, labels[a]) for a in labels if self.can(a) and (a != "confirm" or self.state == self.QUOTATION)]

    NEXT_STEP = {
        "draft": "Review the lines, then send the quotation to the customer.",
        "quotation": "Waiting for the customer to accept. Confirm it once they have.",
        "confirmed": "Accepted. Activate it to start billing from the start date.",
        "active": "Billing runs automatically. Changes apply as previewed and never alter issued invoices.",
        "paused": "Billing is on hold. Resume to continue from the next unbilled period.",
        "closed": "This term has ended. Renew it to start a new linked term.",
        "cancelled": "This quotation was cancelled or rejected. Create a new one if needed.",
    }

    @property
    def next_step(self):
        return self.NEXT_STEP[self.state]

    @property
    def can_edit(self):
        return self.state not in (self.CLOSED, self.CANCELLED)

    @property
    def can_addons(self):
        return self.can("addons")

    @property
    def can_renew_action(self):
        return self.can("renew")

    @property
    def can_close_action(self):
        return self.can("close")

    @property
    def can_change(self):
        return self.can("change")

    def can(self, action):
        s, p = self.state, self.plan
        return {
            "send": s == self.DRAFT,
            "confirm": s == self.QUOTATION,
            "reject": s in (self.DRAFT, self.QUOTATION, self.CONFIRMED),
            "activate": s == self.CONFIRMED,
            "pause": s == self.ACTIVE and p.allow_pause,
            "resume": s == self.PAUSED,
            "close": s in (self.ACTIVE, self.PAUSED) and p.allow_early_close and self.pending_cancel_at is None,
            "renew": s in (self.ACTIVE, self.CLOSED) and p.allow_renew and not self.renewals.exists(),
            "addons": s in (self.ACTIVE, self.CONFIRMED, self.PAUSED),
            "change": s in (self.ACTIVE, self.PAUSED),
        }.get(action, False)


class SubscriptionLine(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    label = models.CharField("Description", max_length=200, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.PROTECT)
    is_plan_line = models.BooleanField(default=False, editable=False)
    recurring = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    @property
    def description(self):
        if self.label:
            return self.label
        name = self.product.name if self.product_id else "Item"
        return f"{name} ({self.variant.value})" if self.variant_id else name

    @property
    def gross_amount(self):
        return q(self.unit_price * self.quantity)

    @property
    def tax_rate(self):
        return self.tax.rate if self.tax_id else Decimal("0")

    @property
    def inclusive(self):
        return bool(self.tax_id and self.tax.inclusive)

    @property
    def net_amount(self):
        """Amount before tax. For tax-inclusive lines the tax is carved out of the listed price."""
        if self.inclusive:
            return q(self.gross_amount / (1 + self.tax_rate / 100))
        return self.gross_amount

    @property
    def amount(self):  # kept for templates: pre-tax line amount
        return self.net_amount

    @property
    def tax_amount(self):
        return q(self.gross_amount - self.net_amount) if self.inclusive else q(self.net_amount * self.tax_rate / 100)

    @property
    def is_addon(self):
        return bool(self.product_id and self.product.is_addon)

    @property
    def is_recurring(self):
        return self.recurring


class SubscriptionChange(models.Model):
    """Effective-dated contract event. MRR movements and history reports are built from these rows,
    so past trends never depend on the subscription's current state."""

    NEW, EXPANSION, CONTRACTION, CHURN, REACTIVATION, PAUSE, NONE = "new", "expansion", "contraction", "churn", "reactivation", "pause", "none"
    KINDS = [("created", "Created"), ("activated", "Activated"), ("paused", "Paused"), ("resumed", "Resumed"), ("renewed", "Renewed"),
             ("closed", "Closed"), ("quantity", "Quantity changed"), ("plan", "Plan changed"), ("addon", "Add-on changed"), ("price", "Price changed"),
             ("terms", "Terms edited"), ("cancel_scheduled", "Cancellation scheduled"), ("rejected", "Rejected"), ("accepted", "Accepted")]

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="changes")
    kind = models.CharField(max_length=20, choices=KINDS)
    effective_date = models.DateField(db_index=True)
    mrr_before = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    mrr_after = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    movement = models.CharField(max_length=12, default=NONE, db_index=True)
    detail = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_date", "id"]
        indexes = [models.Index(fields=["movement", "effective_date"])]

    @property
    def delta(self):
        return self.mrr_after - self.mrr_before


class QuoteAcceptance(models.Model):
    """Consent record for online acceptance of a quotation (who, when, from where, what they agreed to)."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="acceptances")
    revision = models.PositiveIntegerField(default=1)
    accepted_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    signed_name = models.CharField(max_length=160, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    total_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    terms_snapshot = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class ScheduledChange(models.Model):
    """A contract amendment that takes effect at the start of a future billing period."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="scheduled_changes")
    effective_date = models.DateField(db_index=True)
    payload = models.JSONField(help_text="plan_id, quantity, addons {product_id: bool}")
    summary = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["effective_date", "id"]
