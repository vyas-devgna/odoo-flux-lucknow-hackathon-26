from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("catalog/", include("catalog.urls")),
    path("subscriptions/", include("subscriptions.urls")),
    path("billing/", include("billing.urls")),
    path("store/", include("store.urls")),
    path("", include("core.urls")),
]
