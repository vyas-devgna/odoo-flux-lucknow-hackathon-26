from django.utils import timezone

from core.crud import Resource

from . import forms, models

app_name = "catalog"

products = Resource("catalog", "products", models.Product, forms.ProductForm, "Products",
                    columns=[("Name", "name"), ("Type", "get_type_display"), ("Sales price", "sales_price|money"),
                             ("Cost", "cost_price|money"), ("Margin", "margin|money"), ("Variants", "variants.count"), ("Status", "active|state")],
                    search=["name"], inline=forms.VariantFormSet, filters=[("type", "type", models.Product.TYPES)],
                    blurb="What Brightloop sells. Variants add to the base price.")
plans = Resource("catalog", "plans", models.Plan, forms.PlanForm, "Plans",
                 columns=[("Name", "name"), ("Price", "price|money"), ("Billing", "get_interval_display"),
                          ("Min qty", "min_quantity"), ("Offers", "products.count"), ("Status", "active|state")],
                 search=["name"], filters=[("interval", "interval", models.Plan.INTERVALS)],
                 blurb="Price, billing frequency and the rules each subscription must follow.")
taxes = Resource("catalog", "taxes", models.Tax, forms.TaxForm, "Taxes",
                 columns=[("Name", "name"), ("Rate", "rate"), ("Default", "is_default|yes"), ("Status", "active|state")],
                 blurb="Calculated automatically on every invoice line.")
templates = Resource("catalog", "templates", models.QuotationTemplate, forms.TemplateForm, "Quotation templates",
                     columns=[("Name", "name"), ("Plan", "plan"), ("Valid for", "validity_days|days"), ("Lines", "lines.count")],
                     search=["name"], inline=forms.TemplateLineFormSet, write="internal",
                     blurb="Start a quotation from a ready-made plan and product set.")
discounts = Resource("catalog", "discounts", models.Discount, forms.DiscountForm, "Discounts",
                     columns=[("Name", "name"), ("Value", "label"), ("Window", "window"), ("Used", "usage"),
                              ("Status", "status")],
                     search=["name"], blurb="Applied to invoices automatically while eligible. Only admins can create rules.")

urlpatterns = [u for r in (products, plans, taxes, templates, discounts) for u in r.urls()]
RESOURCES = {r.slug: r for r in (products, plans, taxes, templates, discounts)}
