from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", User.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    ADMIN, STAFF, CUSTOMER = "admin", "staff", "customer"
    ROLES = [(ADMIN, "Admin"), (STAFF, "Internal staff"), (CUSTOMER, "Customer")]

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120, blank=True)
    role = models.CharField(max_length=10, choices=ROLES, default=CUSTOMER)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin site access only
    # Staff capability flags (admins implicitly hold all of them)
    can_sales = models.BooleanField("Sales", default=True, help_text="Customers, quotations and subscription changes.")
    can_billing = models.BooleanField("Billing", default=True, help_text="Invoices, credit notes and billing runs.")
    can_finance = models.BooleanField("Finance", default=False, help_text="Record payments and refunds, view financial reports.")
    can_catalog = models.BooleanField("Catalogue", default=False, help_text="Edit products, plans, templates and taxes.")
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="uniq_user_email_ci")]

    def __str__(self):
        return self.name or self.email

    @property
    def is_admin_role(self):
        return self.role == self.ADMIN

    PERMS = ("sales", "billing", "finance", "catalog")

    def has_cap(self, cap):
        if self.role == self.ADMIN:
            return True
        return self.role == self.STAFF and bool(getattr(self, f"can_{cap}", False))

    @property
    def is_internal(self):
        return self.role in (self.ADMIN, self.STAFF)

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class Customer(models.Model):
    """Billing identity. Staff can quote before the customer has a login."""

    name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    company = models.CharField(max_length=160, blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField("Tax ID", max_length=40, blank=True)
    account_manager = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_customers")
    notes = models.TextField(blank=True)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="customer")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return self.company or self.name

    @property
    def display_name(self):
        return f"{self.name} · {self.company}" if self.company else self.name


class LoginAttempt(models.Model):
    """Failed sign-ins, used to throttle guessing per email and per IP."""

    email = models.CharField(max_length=254, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    LIMIT, WINDOW_MIN = 5, 15

    @classmethod
    def blocked(cls, email, ip):
        from datetime import timedelta
        from django.db.models import Q
        since = timezone.now() - timedelta(minutes=cls.WINDOW_MIN)
        recent = cls.objects.filter(at__gte=since)
        return (recent.filter(email=email).count() >= cls.LIMIT) or (recent.filter(ip=ip).count() >= cls.LIMIT * 4)


class PaymentMethod(models.Model):
    """A customer's preferred way to pay. Descriptive only: no card or bank secrets are ever stored."""

    KINDS = [("upi", "UPI"), ("bank", "Bank transfer"), ("card", "Card (via payment provider)"), ("cheque", "Cheque")]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="payment_methods")
    kind = models.CharField(max_length=8, choices=KINDS, default="bank")
    label = models.CharField(max_length=80, help_text="e.g. UPI ID or bank nickname")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.label}"
