from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Tax(models.Model):
    name = models.CharField(max_length=80)
    rate = models.DecimalField("Rate %", max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    inclusive = models.BooleanField("Price includes this tax", default=False,
                                    help_text="When on, listed prices already contain the tax; invoices split it out.")
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=False, help_text="Applied to new lines automatically.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "taxes"

    def __str__(self):
        return f"{self.name} ({self.rate.normalize():f}%{', incl.' if self.inclusive else ''})"

    def clean(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError({"valid_to": "Must be on or after the start date."})

    def applies_on(self, day):
        return self.active and (not self.valid_from or self.valid_from <= day) and (not self.valid_to or day <= self.valid_to)

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.is_default:
            Tax.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls, day=None):
        day = day or timezone.localdate()
        for t in cls.objects.filter(active=True, is_default=True):
            if t.applies_on(day):
                return t
        return None


class Product(models.Model):
    SOFTWARE, SERVICE, ADDON, PHYSICAL, DIGITAL = "software", "service", "addon", "physical", "digital"
    TYPES = [(SOFTWARE, "Software"), (SERVICE, "Service"), (ADDON, "Add-on"), (DIGITAL, "Digital"), (PHYSICAL, "Physical")]

    name = models.CharField(max_length=160)
    sku = models.CharField("SKU", max_length=40, blank=True)
    type = models.CharField(max_length=10, choices=TYPES, default=SOFTWARE)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    description = models.TextField(blank=True)
    uom = models.CharField("Unit", max_length=20, default="seat", help_text="Unit of measure, e.g. seat, GB, hour.")
    sales_price = models.DecimalField(**MONEY)
    cost_price = models.DecimalField(**MONEY, default=0)
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Blank uses the default tax.")
    recurring = models.BooleanField("Recurring", default=True, help_text="Billed every period. Off for one-time items.")
    published = models.BooleanField("Show in storefront", default=False)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["sku"], condition=~models.Q(sku=""), name="uniq_product_sku")]

    def __str__(self):
        return self.name

    @property
    def is_addon(self):
        return self.type == self.ADDON

    @property
    def margin(self):
        return self.sales_price - self.cost_price


class ProductVariant(models.Model):
    """e.g. attribute 'Brand', value 'Premium', extra price 560 on top of the product price."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    attribute = models.CharField(max_length=60, default="Edition")
    value = models.CharField(max_length=60)
    extra_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["attribute", "extra_price", "value"]
        constraints = [models.UniqueConstraint(fields=["product", "attribute", "value"], name="uniq_variant")]

    def __str__(self):
        return f"{self.attribute}: {self.value}"

    @property
    def price(self):
        return self.product.sales_price + self.extra_price


class Plan(models.Model):
    DAILY, WEEKLY, MONTHLY, YEARLY = "daily", "weekly", "monthly", "yearly"
    INTERVALS = [(DAILY, "Daily"), (WEEKLY, "Weekly"), (MONTHLY, "Monthly"), (YEARLY, "Yearly")]
    # periods per month, for normalising recurring revenue (daily uses a 30.4375-day average month)
    MONTHLY_FACTOR = {DAILY: Decimal("30.4375"), WEEKLY: Decimal("4.348214"), MONTHLY: Decimal("1"), YEARLY: Decimal("0.083333")}
    RENEW_AUTO, RENEW_MANUAL = "auto", "manual"
    CANCEL_ANYTIME, CANCEL_PERIOD_END = "anytime", "period_end"
    ACT_MANUAL, ACT_ACCEPT, ACT_PAYMENT = "manual", "acceptance", "payment"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField("Base price per unit", **MONEY)
    interval = models.CharField("Billing frequency", max_length=8, choices=INTERVALS, default=MONTHLY)
    min_quantity = models.PositiveIntegerField(default=1)
    available_from = models.DateField(null=True, blank=True)
    available_to = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=10, default="net15", choices=[("immediate", "Immediate"), ("net15", "Net 15"), ("net30", "Net 30"), ("net60", "Net 60")])
    renewal_policy = models.CharField(max_length=6, choices=[(RENEW_AUTO, "Renews automatically"), (RENEW_MANUAL, "Renewal needs approval")], default=RENEW_MANUAL)
    renewal_notice_days = models.PositiveIntegerField("Renewal notice (days)", default=14)
    min_duration_periods = models.PositiveIntegerField("Minimum contract (billing periods)", default=0, help_text="0 for none. Early closure is blocked inside this window.")
    cancel_policy = models.CharField("Cancellation takes effect", max_length=10, choices=[(CANCEL_ANYTIME, "Immediately"), (CANCEL_PERIOD_END, "At the end of the paid period")], default=CANCEL_PERIOD_END)
    auto_close = models.BooleanField("Close automatically at expiry", default=True)
    allow_early_close = models.BooleanField("Allow early closing", default=True)
    allow_pause = models.BooleanField("Allow pausing", default=True)
    allow_renew = models.BooleanField("Allow renewal", default=True)
    self_serve_changes = models.BooleanField("Customers can change plan and seats", default=True)
    self_serve_cancel = models.BooleanField("Customers can cancel", default=True)
    activation = models.CharField("Activate", max_length=10, choices=[(ACT_MANUAL, "Manually by staff"), (ACT_ACCEPT, "When the customer accepts"), (ACT_PAYMENT, "When the first invoice is paid")], default=ACT_MANUAL)
    published = models.BooleanField("Show in storefront", default=False)
    products = models.ManyToManyField(Product, blank=True, related_name="plans", help_text="Products and add-ons offered on this plan.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} · {self.get_interval_display()}"

    def clean(self):
        if self.available_from and self.available_to and self.available_to < self.available_from:
            raise ValidationError({"available_to": "Must be on or after the start date."})

    def is_available(self, on=None):
        on = on or timezone.localdate()
        return (self.active and (not self.available_from or self.available_from <= on)
                and (not self.available_to or on <= self.available_to))

    @property
    def billing_unit(self):
        return {self.DAILY: "day", self.WEEKLY: "week", self.MONTHLY: "month", self.YEARLY: "year"}[self.interval]

    def unit_price_for(self, quantity):
        """Base price per unit for a quantity: the highest tier at or below it, else the plan's base price."""
        tiers = getattr(self, "_prefetched_objects_cache", {}).get("tiers")
        if tiers is not None:
            tier = max((t for t in tiers if t.min_quantity <= quantity), key=lambda t: t.min_quantity, default=None)
            return tier.unit_price if tier else self.price
        tier = self.tiers.filter(min_quantity__lte=quantity).order_by("-min_quantity").first()
        return tier.unit_price if tier else self.price


class PlanTier(models.Model):
    """Quantity-based pricing: from `min_quantity` units, each unit costs `unit_price`."""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="tiers")
    min_quantity = models.PositiveIntegerField(validators=[MinValueValidator(2)])
    unit_price = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["min_quantity"]
        constraints = [models.UniqueConstraint(fields=["plan", "min_quantity"], name="uniq_plan_tier")]


class QuotationTemplate(models.Model):
    name = models.CharField(max_length=120)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="templates")
    validity_days = models.PositiveIntegerField(default=30)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TemplateLine(models.Model):
    template = models.ForeignKey(QuotationTemplate, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    def clean(self):
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": "Variant belongs to a different product."})

    @property
    def unit_price(self):
        return self.variant.price if self.variant_id else self.product.sales_price


class Discount(models.Model):
    FIXED, PERCENT = "fixed", "percent"
    KINDS = [(FIXED, "Fixed amount"), (PERCENT, "Percentage")]
    WHOLE, PRODUCTS = "subscription", "products"
    SCOPES = [(WHOLE, "Whole subscription"), (PRODUCTS, "Selected products only")]

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=8, choices=KINDS, default=PERCENT)
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    scope = models.CharField("Applies to", max_length=12, choices=SCOPES, default=WHOLE)
    products = models.ManyToManyField(Product, blank=True, related_name="discounts")
    plans = models.ManyToManyField(Plan, blank=True, related_name="discounts", help_text="Limit to these plans. Empty means every plan.")
    min_purchase = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Minimum eligible subtotal per invoice.")
    min_quantity = models.PositiveIntegerField(default=0, help_text="Minimum eligible units per invoice.")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited.")
    times_used = models.PositiveIntegerField(default=0, editable=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.kind == self.PERCENT and self.value and self.value > 100:
            raise ValidationError({"value": "A percentage cannot exceed 100."})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Must be on or after the start date."})

    @property
    def label(self):
        return f"{self.value.normalize():f}%" if self.kind == self.PERCENT else f"₹{self.value}"

    @property
    def window(self):
        end = f"{self.end_date:%d %b %Y}" if self.end_date else "no end"
        return f"{self.start_date:%d %b %Y} → {end}"

    @property
    def usage(self):
        return f"{self.times_used} / {self.usage_limit}" if self.usage_limit is not None else f"{self.times_used} / ∞"

    @property
    def exhausted(self):
        return self.usage_limit is not None and self.times_used >= self.usage_limit

    def status(self, on=None):
        on = on or timezone.localdate()
        if not self.active:
            return "inactive"
        if self.start_date > on:
            return "scheduled"
        if self.end_date and self.end_date < on:
            return "expired"
        if self.exhausted:
            return "exhausted"
        return "live"
