from django.urls import path

from . import views

app_name = "subscriptions"
urlpatterns = [
    path("", views.subscription_list, name="list"),
    path("new/", views.subscription_new, name="new"),
    path("<int:pk>/", views.subscription_detail, name="detail"),
    path("<int:pk>/edit/", views.subscription_edit, name="edit"),
    path("<int:pk>/print/", views.subscription_print, name="print"),
    path("<int:pk>/<str:action>/", views.subscription_action, name="action"),
]
