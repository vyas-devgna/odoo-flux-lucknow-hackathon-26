from django.urls import path

from . import views

app_name = "subscriptions"
urlpatterns = [
    path("", views.subscription_list, name="list"),
    path("quotations/", views.quotes, name="quotes"),
    path("pipeline/", views.pipeline, name="pipeline"),
    path("renewals/", views.renewals, name="renewals"),
    path("changes/", views.changes, name="changes"),
    path("new/", views.subscription_new, name="new"),
    path("<int:pk>/", views.subscription_detail, name="detail"),
    path("<int:pk>/edit/", views.subscription_edit, name="edit"),
    path("<int:pk>/change/", views.subscription_change, name="change"),
    path("<int:pk>/print/", views.subscription_print, name="print"),
    path("<int:pk>/<str:action>/", views.subscription_action, name="action"),
]
