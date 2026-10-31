from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse

from core import audit, mail

from .models import Customer, User

VERIFY_SALT = "accounts.customer-link"


def send_customer_verification(user):
    customer = Customer.objects.filter(email__iexact=user.email, user__isnull=True).first()
    if customer is None:
        return
    token = signing.dumps({"user": user.pk, "customer": customer.pk}, salt=VERIFY_SALT)
    url = settings.SITE_URL + reverse("accounts:verify_customer", args=[token])
    mail.queue_email(user.email, "Verify your Brightloop email",
                     f"Hello {user.name},\n\nConfirm your email to access your existing billing profile:\n{url}\n\n"
                     "This link expires in one hour. If you did not register, do not follow this link.\n\nBrightloop Studio")


@transaction.atomic
def verify_customer(token):
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=3600)
        user = User.objects.select_for_update().get(pk=data["user"], role=User.CUSTOMER, is_active=True)
        customer = Customer.objects.select_for_update().get(pk=data["customer"], email__iexact=user.email)
    except (signing.BadSignature, KeyError, TypeError, ValueError, User.DoesNotExist, Customer.DoesNotExist):
        raise ValidationError("This verification link is invalid or expired. Sign in and request a new link.") from None
    if customer.user_id == user.pk:
        return user
    if customer.user_id is not None or Customer.objects.filter(user=user).exists():
        raise ValidationError("This billing profile is already linked. Please contact your account manager.")
    customer.user = user
    customer.save(update_fields=["user"])
    audit.log(user, "account.email_verified", customer)
    return user
