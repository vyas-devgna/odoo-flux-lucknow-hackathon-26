from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class Product(models.Model):
    SOFTWARE, SERVICE, ADDON, PHYSICAL = "software", "service", "addon", "physical"
    TYPES = [(SOFTWARE, "Software"), (SERVICE, "Service"), (ADDON, "Add-on"), (PHYSICAL, "Physical")]

    name = models.CharField(max_length=160)
    type = models.CharField(max_length=10, choices=TYPES, default=SOFTWARE)
    sales_price = models.DecimalField(**MONEY)
    cost_price = models.DecimalField(**MONEY, default=0)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

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
        ordering = ["attribute", "value"]
        constraints = [models.UniqueConstraint(fields=["product", "attribute", "value"], name="uniq_variant")]

    def __str__(self):
        return f"{self.attribute}: {self.value}"

    @property
    def price(self):
        return self.product.sales_price + self.extra_price


class Tax(models.Model):
    name = models.CharField(max_length=80)
    rate = models.DecimalField("Rate %", max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    is_default = models.BooleanField(default=False, help_text="Applied to new lines automatically.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "taxes"

    def __str__(self):
        return f"{self.name} ({self.rate.normalize():f}%)"

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.is_default:
            Tax.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls):
        return cls.objects.filter(active=True, is_default=True).first()


class Plan(models.Model):
    DAILY, WEEKLY, MONTHLY, YEARLY = "daily", "weekly", "monthly", "yearly"
    INTERVALS = [(DAILY, "Daily"), (WEEKLY, "Weekly"), (MONTHLY, "Monthly"), (YEARLY, "Yearly")]
    # Normalised multiplier to monthly for MRR reporting
    MONTHLY_FACTOR = {DAILY: Decimal("30"), WEEKLY: Decimal("4.345"), MONTHLY: Decimal("1"), YEARLY: Decimal("0.083333")}

    name = models.CharField(max_length=120)
    price = models.DecimalField("Price per seat", **MONEY)
    interval = models.CharField("Billing frequency", max_length=8, choices=INTERVALS, default=MONTHLY)
    min_quantity = models.PositiveIntegerField(default=1)
    available_from = models.DateField(null=True, blank=True)
    available_to = models.DateField(null=True, blank=True)
    auto_close = models.BooleanField("Close automatically at expiry", default=True)
    allow_early_close = models.BooleanField("Allow early closing", default=True)
    allow_pause = models.BooleanField("Allow pausing", default=True)
    allow_renew = models.BooleanField("Allow renewal", default=True)
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

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=8, choices=KINDS, default=PERCENT)
    value = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    min_purchase = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Minimum eligible subtotal per invoice.")
    min_quantity = models.PositiveIntegerField(default=0, help_text="Minimum eligible units per invoice.")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited.")
    times_used = models.PositiveIntegerField(default=0, editable=False)
    products = models.ManyToManyField(Product, blank=True, related_name="discounts",
                                      help_text="Leave empty to discount the whole subscription.")
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
