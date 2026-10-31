from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("reports/", views.finance_reports, name="reports"),
    path("customers/", views.customer_list, name="customers"),
    path("customers/new/", views.customer_form, name="customer_new"),
    path("customers/<int:pk>/", views.customer_form, name="customer_edit"),
    path("customers/<int:pk>/statement/", views.customer_statement, name="customer_statement"),
    path("settings/", views.company_settings, name="settings"),
    path("audit/", views.audit_log, name="audit"),
    path("search/", views.search, name="search"),
    path("export/<str:what>/", views.export, name="export"),
    path("healthz/", views.healthz, name="healthz"),
    path("portal/profile/", views.portal_profile, name="portal_profile"),
    path("portal/", views.portal, name="portal"),
    path("portal/subscriptions/", views.portal_subscriptions, name="portal_subscriptions"),
    path("portal/subscriptions/<int:pk>/", views.portal_subscription, name="portal_subscription"),
    path("portal/subscriptions/<int:pk>/<str:action>/", views.portal_action, name="portal_action"),
    path("portal/invoices/", views.portal_invoices, name="portal_invoices"),
    path("portal/invoices/<int:pk>/", views.portal_invoice, name="portal_invoice"),
    path("portal/invoices/<int:pk>/pay/", views.portal_pay, name="portal_pay"),
    path("portal/invoices/<int:pk>/print/", views.portal_invoice_print, name="portal_invoice_print"),
]
