from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from core import audit
from core.permissions import admin_required

from .forms import LoginForm, SignupForm, StaffForm
from .models import User


class Login(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        from .models import LoginAttempt
        LoginAttempt.objects.filter(email=form.get_user().email).delete()
        audit.log(form.get_user(), "account.login", form.get_user())
        return super().form_valid(form)


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        audit.log(user, "account.signup", user)
        login(request, user, backend="accounts.backends.EmailBackend")
        messages.success(request, "Welcome to Brightloop. Your account is ready.")
        return redirect("core:home")
    return render(request, "accounts/signup.html", {"form": form})


@admin_required
def team(request):
    members = User.objects.exclude(role=User.CUSTOMER).order_by("role", "name")
    return render(request, "accounts/team.html", {"members": members})


@admin_required
def team_form(request, pk=None):
    member = User.objects.exclude(role=User.CUSTOMER).get(pk=pk) if pk else None
    form = StaffForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        audit.log(request.user, "team.save", saved, f"role {saved.role}, active {saved.is_active}")
        messages.success(request, "Team member saved.")
        return redirect("accounts:team")
    return render(request, "accounts/team_form.html", {"form": form, "member": member})


from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash


@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs["class"] = "input"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit.log(user, "account.password_change", user)
        messages.success(request, "Password updated.")
        return redirect("core:home")
    return render(request, "accounts/password_change.html", {"form": form})
