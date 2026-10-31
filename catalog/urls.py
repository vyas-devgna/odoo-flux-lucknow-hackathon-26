from core.crud import Resource

from . import forms, models

app_name = "catalog"

# write="catalog": admins and staff with the Catalogue capability. Discounts stay admin-only by brief.
products = Resource("catalog", "products", models.Product, forms.ProductForm, "Products",
                    columns=[("Name", "name"), ("Type", "get_type_display"), ("Category", "category"), ("Price", "sales_price|money"), ("Margin", "margin|money"), ("Variants", "variants.count"), ("Status", "active|state")],
                    search=["name", "sku"], inline=forms.VariantFormSet, filters=[("type", "type", models.Product.TYPES)], write="catalog",
                    queryset=lambda: models.Product.objects.select_related("category"), blurb="What you sell. Variants add to the base price; add-ons can be switched on and off.",
                    sections=[("Product", ["name", "sku", "type", "category", "description", "uom", "active"]), ("Pricing", ["sales_price", "cost_price", "tax", "recurring"]), ("Storefront and notes", ["published", "notes"])])
plans = Resource("catalog", "plans", models.Plan, forms.PlanForm, "Plans",
                 columns=[("Name", "name"), ("Price", "price|money"), ("Billing", "get_interval_display"), ("Min qty", "min_quantity"), ("Renewal", "get_renewal_policy_display"), ("Offers", "products.count"), ("Status", "active|state")],
                 search=["name"], inline=forms.TierFormSet, filters=[("interval", "interval", models.Plan.INTERVALS)], write="catalog",
                 blurb="Price, billing frequency, contract rules and what customers can do themselves.",
                 sections=[("Plan", ["name", "description", "price", "interval", "min_quantity", "payment_terms", "active"]), ("Availability", ["available_from", "available_to"]),
                           ("Contract rules", ["renewal_policy", "renewal_notice_days", "min_duration_periods", "cancel_policy", "auto_close", "allow_early_close", "allow_pause", "allow_renew"]),
                           ("Customers and storefront", ["self_serve_changes", "self_serve_cancel", "activation", "published"]), ("Offered with this plan", ["products"])])
taxes = Resource("catalog", "taxes", models.Tax, forms.TaxForm, "Taxes",
                 columns=[("Name", "name"), ("Rate", "rate"), ("Inclusive", "inclusive|yes"), ("Default", "is_default|yes"), ("Status", "active|state")], write="catalog",
                 blurb="Calculated automatically on every line. Default applies to new lines; products can override it.")
categories = Resource("catalog", "categories", models.Category, forms.CategoryForm, "Categories", columns=[("Name", "name"), ("Products", "products.count")], search=["name"], write="catalog",
                      blurb="Group products for reporting and the storefront.")
templates = Resource("catalog", "templates", models.QuotationTemplate, forms.TemplateForm, "Quotation templates",
                     columns=[("Name", "name"), ("Plan", "plan"), ("Valid for", "validity_days|days"), ("Lines", "lines.count")],
                     search=["name"], inline=forms.TemplateLineFormSet, write="sales", blurb="Start a quotation from a ready-made plan and product set.")
discounts = Resource("catalog", "discounts", models.Discount, forms.DiscountForm, "Discounts",
                     columns=[("Name", "name"), ("Value", "label"), ("Window", "window"), ("Used", "usage"), ("Status", "status")],
                     search=["name"], write="admin", blurb="Applied to invoices automatically while eligible. Only admins can create rules.",
                     sections=[("Discount", ["name", "kind", "value", "active"]), ("Where it applies", ["scope", "products", "plans"]), ("Conditions and limits", ["min_purchase", "min_quantity", "start_date", "end_date", "usage_limit"])])

RESOURCES = {r.slug: r for r in (products, plans, taxes, categories, templates, discounts)}
urlpatterns = [u for r in RESOURCES.values() for u in r.urls()]
