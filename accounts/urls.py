from django.contrib.auth import views as auth
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.Login.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("reset/", auth.PasswordResetView.as_view(
        template_name="accounts/reset.html", email_template_name="accounts/reset_email.txt",
        subject_template_name="accounts/reset_subject.txt",
        success_url=reverse_lazy("accounts:reset_done")), name="reset"),
    path("reset/sent/", auth.PasswordResetDoneView.as_view(template_name="accounts/reset_done.html"), name="reset_done"),
    path("reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(
        template_name="accounts/reset_confirm.html", success_url=reverse_lazy("accounts:reset_complete")), name="reset_confirm"),
    path("reset/complete/", auth.PasswordResetCompleteView.as_view(template_name="accounts/reset_complete.html"), name="reset_complete"),
    path("team/", views.team, name="team"),
    path("team/new/", views.team_form, name="team_new"),
    path("team/<int:pk>/", views.team_form, name="team_edit"),
]
