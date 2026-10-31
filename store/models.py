from django.db import models


class CheckoutReceipt(models.Model):
    """A checkout request keeps its contract identity across browser and server retries."""

    key = models.UUIDField(primary_key=True, editable=False)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT)
    subscription = models.OneToOneField("subscriptions.Subscription", on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
