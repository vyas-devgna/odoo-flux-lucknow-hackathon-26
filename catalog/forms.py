from django import forms
from django.forms import inlineformset_factory

from core.crud import StyledModelForm

from .models import Category, Discount, Plan, PlanTier, Product, ProductVariant, QuotationTemplate, TemplateLine, Tax


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "type", "category", "description", "uom", "sales_price", "cost_price", "tax", "recurring", "published", "notes", "active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 2})}


VariantFormSet = inlineformset_factory(Product, ProductVariant, fields=["attribute", "value", "extra_price"], extra=1, can_delete=True)


class TaxForm(StyledModelForm):
    class Meta:
        model = Tax
        fields = ["name", "rate", "inclusive", "valid_from", "valid_to", "is_default", "active"]
        widgets = {"valid_from": forms.DateInput(), "valid_to": forms.DateInput()}


class PlanForm(StyledModelForm):
    class Meta:
        model = Plan
        fields = ["name", "description", "price", "interval", "min_quantity", "available_from", "available_to", "payment_terms", "renewal_policy",
                  "renewal_notice_days", "min_duration_periods", "cancel_policy", "auto_close", "allow_early_close", "allow_pause", "allow_renew",
                  "self_serve_changes", "self_serve_cancel", "activation", "published", "products", "active"]
        widgets = {"available_from": forms.DateInput(), "available_to": forms.DateInput(), "description": forms.Textarea(attrs={"rows": 2})}


TierFormSet = inlineformset_factory(Plan, PlanTier, fields=["min_quantity", "unit_price"], extra=1, can_delete=True)


class CategoryForm(StyledModelForm):
    class Meta:
        model = Category
        fields = ["name"]


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
        fields = ["name", "kind", "value", "scope", "products", "plans", "min_purchase", "min_quantity", "start_date", "end_date",
                  "usage_limit", "active"]
        widgets = {"start_date": forms.DateInput(), "end_date": forms.DateInput(), "scope": forms.RadioSelect()}

    def clean(self):
        d = super().clean()
        if d.get("scope") == Discount.PRODUCTS and not d.get("products"):
            self.add_error("products", "Pick at least one product, or apply to the whole subscription.")
        if d.get("scope") == Discount.WHOLE:
            d["products"] = []
        return d
