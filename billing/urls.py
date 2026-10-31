from django.urls import path

from . import views

app_name = "billing"
urlpatterns = [
    path("invoices/", views.invoice_list, name="list"),
    path("invoices/remind/", views.bulk_remind, name="bulk_remind"),
    path("invoices/confirm-drafts/", views.bulk_confirm, name="bulk_confirm"),
    path("invoices/<int:pk>/", views.invoice_detail, name="detail"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="print"),
    path("invoices/<int:pk>/<str:action>/", views.invoice_action, name="action"),
    path("payments/", views.payment_list, name="payments"),
    path("runs/", views.billing_runs, name="runs"),
]
