from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .models import Customer

User = get_user_model()


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))

    def clean(self):
        from .models import LoginAttempt
        email = (self.data.get("username") or "").strip().lower()
        ip = self.request.META.get("REMOTE_ADDR") if self.request else None
        if LoginAttempt.blocked(email, ip):
            raise forms.ValidationError(f"Too many failed attempts. Try again in {LoginAttempt.WINDOW_MIN} minutes or reset your password.", code="throttled")
        try:
            return super().clean()
        except forms.ValidationError:
            LoginAttempt.objects.create(email=email, ip=ip)
            raise


class SignupForm(forms.Form):
    name = forms.CharField(max_length=120)
    company = forms.CharField(max_length=160, required=False)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                               help_text="More than 8 characters with an uppercase letter, a lowercase letter and a special character.")
    confirm = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        if data.get("password") and data["password"] != data.get("confirm"):
            self.add_error("confirm", "Passwords do not match.")
        elif data.get("password"):
            try:
                validate_password(data["password"])
            except forms.ValidationError as e:
                self.add_error("password", e)
        return data

    def save(self):
        d = self.cleaned_data
        user = User.objects.create_user(d["email"], d["password"], name=d["name"], role=User.CUSTOMER)
        # Adopt a customer record that staff prepared for this email, otherwise create one.
        cust = Customer.objects.filter(email__iexact=d["email"], user__isnull=True).first()
        if cust:
            cust.user = user
            cust.save(update_fields=["user"])
        else:
            Customer.objects.create(user=user, name=d["name"], email=d["email"], company=d["company"])
        return user


class StaffForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text="Required for a new member; leave blank to keep the current one.")

    class Meta:
        model = User
        fields = ["name", "email", "role", "can_sales", "can_billing", "can_finance", "can_catalog", "is_active"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["role"].choices = [(User.STAFF, "Internal staff"), (User.ADMIN, "Admin")]

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if not self.instance.pk and not pw:
            raise forms.ValidationError("Set an initial password.")
        if pw:
            validate_password(pw)
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data["password"]:
            user.set_password(self.cleaned_data["password"])
        user.is_staff = user.role == User.ADMIN
        user.is_superuser = user.role == User.ADMIN
        if commit:
            user.save()
        return user


class CustomerForm(forms.ModelForm):
    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        dup = Customer.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(f"A customer with this email already exists ({dup.first().display_name}).")
        return email

    class Meta:
        model = Customer
        fields = ["name", "company", "email", "phone", "address", "tax_id"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs["class"] = "input"


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        from .models import PaymentMethod
        model = PaymentMethod
        fields = ["kind", "label", "is_default"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs["class"] = "input"
