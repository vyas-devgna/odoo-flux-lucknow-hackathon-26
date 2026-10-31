from django import forms
from django.forms import inlineformset_factory

from core.crud import StyledModelForm

from .models import Discount, Plan, Product, ProductVariant, QuotationTemplate, TemplateLine, Tax


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = ["name", "type", "sales_price", "cost_price", "tax", "notes", "active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


VariantFormSet = inlineformset_factory(Product, ProductVariant, fields=["attribute", "value", "extra_price"], extra=1, can_delete=True)


class TaxForm(StyledModelForm):
    class Meta:
        model = Tax
        fields = ["name", "rate", "is_default", "active"]


class PlanForm(StyledModelForm):
    class Meta:
        model = Plan
        fields = ["name", "price", "interval", "min_quantity", "available_from", "available_to", "auto_close",
                  "allow_early_close", "allow_pause", "allow_renew", "products", "active"]
        widgets = {"available_from": forms.DateInput(), "available_to": forms.DateInput()}


class TemplateForm(StyledModelForm):
    class Meta:
        model = QuotationTemplate
        fields = ["name", "plan", "validity_days", "notes", "active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class TemplateLineForm(StyledModelForm):
    class Meta:
        model = TemplateLine
        fields = ["product", "variant", "quantity"]


TemplateLineFormSet = inlineformset_factory(QuotationTemplate, TemplateLine, form=TemplateLineForm, extra=1, can_delete=True)


class DiscountForm(StyledModelForm):
    class Meta:
        model = Discount
        fields = ["name", "kind", "value", "scope", "products", "min_purchase", "min_quantity", "start_date", "end_date",
                  "usage_limit", "active"]
        widgets = {"start_date": forms.DateInput(), "end_date": forms.DateInput(), "scope": forms.RadioSelect()}

    def clean(self):
        d = super().clean()
        if d.get("scope") == Discount.PRODUCTS and not d.get("products"):
            self.add_error("products", "Pick at least one product, or apply to the whole subscription.")
        if d.get("scope") == Discount.WHOLE:
            d["products"] = []
        return d
