from django import forms
from django.forms import inlineformset_factory

from accounts.models import Customer
from catalog.models import Plan, QuotationTemplate
from core.crud import StyledModelForm

from .models import Subscription, SubscriptionLine


class NewSubscriptionForm(StyledModelForm):
    template = forms.ModelChoiceField(QuotationTemplate.objects.filter(active=True), required=False,
                                      help_text="Optional: copies the template's product lines and validity.")

    class Meta:
        model = Subscription
        fields = ["customer", "plan", "template", "start_date", "expiry_date", "payment_terms", "notes"]
        widgets = {"start_date": forms.DateInput(), "expiry_date": forms.DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["plan"].queryset = Plan.objects.filter(active=True)
        self.fields["customer"].queryset = Customer.objects.all()

    def clean(self):
        d = super().clean()
        plan, tpl = d.get("plan"), d.get("template")
        if plan and not plan.is_available(d.get("start_date")):
            self.add_error("plan", "This plan isn't available on that start date.")
        if plan and tpl and tpl.plan_id != plan.pk:
            self.add_error("template", f"That template belongs to “{tpl.plan.name}”.")
        if d.get("start_date") and d.get("expiry_date") and d["expiry_date"] <= d["start_date"]:
            self.add_error("expiry_date", "Expiry must be after the start date.")
        return d


class SubscriptionHeaderForm(StyledModelForm):
    class Meta:
        model = Subscription
        fields = ["customer", "start_date", "expiry_date", "payment_terms", "quote_valid_until", "notes"]
        widgets = {"start_date": forms.DateInput(), "expiry_date": forms.DateInput(),
                   "quote_valid_until": forms.DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}


class LiveSubscriptionForm(StyledModelForm):
    """Once a subscription is confirmed the start date and customer are fixed; the billing schedule depends on them."""

    class Meta:
        model = Subscription
        fields = ["expiry_date", "payment_terms", "notes"]
        widgets = {"expiry_date": forms.DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def clean_expiry_date(self):
        d = self.cleaned_data["expiry_date"]
        if d and d <= self.instance.start_date:
            raise forms.ValidationError("Expiry must be after the start date.")
        return d


class LineForm(StyledModelForm):
    class Meta:
        model = SubscriptionLine
        fields = ["product", "variant", "label", "quantity", "unit_price", "tax"]

    def clean(self):
        d = super().clean()
        if d.get("variant") and d.get("product") and d["variant"].product_id != d["product"].pk:
            self.add_error("variant", "Variant belongs to a different product.")
        if not d.get("product") and not d.get("label") and not self.errors:
            self.add_error("label", "Pick a product or describe the line.")
        return d


class BaseLineFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        plan = self.instance.plan if self.instance.pk else None
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not kept and not self.errors:
            raise forms.ValidationError("A subscription needs at least one line.")
        for f in kept:
            d = f.cleaned_data
            if plan and not d.get("product") and d.get("label") == plan.name and d.get("quantity", 0) < plan.min_quantity:
                f.add_error("quantity", f"Plan minimum is {plan.min_quantity}.")


LineFormSet = inlineformset_factory(Subscription, SubscriptionLine, form=LineForm, formset=BaseLineFormSet, extra=1, can_delete=True)
