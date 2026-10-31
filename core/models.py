from django.conf import settings
from django.db import models


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

    name = models.CharField("Company name", max_length=160, default="Brightloop")
    address = models.TextField(blank=True)
    tax_id = models.CharField("Tax ID", max_length=40, blank=True)
    email = models.EmailField("Billing email", blank=True)
    phone = models.CharField(max_length=30, blank=True)
    bank_details = models.TextField("Payment instructions", blank=True, help_text="Shown on invoices, e.g. bank account or UPI ID.")
    invoice_footer = models.CharField(max_length=300, blank=True, default="Thank you for your business.")
    reminder_days_before_renewal = models.PositiveIntegerField("Renewal notice (days before expiry)", default=14)
    reminder_repeat_days = models.PositiveIntegerField("Repeat overdue reminders every (days)", default=7)

    class Meta:
        verbose_name_plural = "company settings"

    def save(self, *a, **kw):
        self.pk = 1
        super().save(*a, **kw)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]
