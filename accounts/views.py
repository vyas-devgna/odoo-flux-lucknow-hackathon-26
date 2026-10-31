from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from core.permissions import admin_required

from .forms import LoginForm, SignupForm, StaffForm
from .models import User


class Login(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
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
        form.save()
        messages.success(request, "Team member saved.")
        return redirect("accounts:team")
    return render(request, "accounts/team_form.html", {"form": form, "member": member})
