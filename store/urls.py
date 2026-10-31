from django.urls import path

from . import views

app_name = "store"
urlpatterns = [
    path("", views.home, name="home"),
    path("plans/<int:pk>/", views.plan_detail, name="plan"),
    path("plans/<int:pk>/checkout/", views.checkout, name="checkout"),
]
