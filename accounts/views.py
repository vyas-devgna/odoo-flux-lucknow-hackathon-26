from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, PasswordResetView
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
    from django.utils.http import url_has_allowed_host_and_scheme

    from core.models import RateLimitHit
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if request.user.is_authenticated:
        return redirect("core:home")
    form = SignupForm(request.POST or None)
    ip = request.META.get("REMOTE_ADDR", "")
    if request.method == "POST":
        if RateLimitHit.count("signup", ip, 60) >= 10:
            form.add_error(None, "Too many sign-ups from this network. Please try again later.")
        elif form.is_valid():
            RateLimitHit.hit("signup", ip)
            user = form.save()
            audit.log(user, "account.signup", user)
            login(request, user, backend="accounts.backends.EmailBackend")
            messages.success(request, "Welcome. Your account is ready.")
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
                return redirect(nxt)
            return redirect("core:home")
    return render(request, "accounts/signup.html", {"form": form, "next": nxt})


class ThrottledReset(PasswordResetView):
    """Password reset requests are limited per address and per network so the form can't be used to spam inboxes."""

    def form_valid(self, form):
        from core.models import RateLimitHit
        email = form.cleaned_data["email"].strip().lower()
        ip = self.request.META.get("REMOTE_ADDR", "")
        if RateLimitHit.count("reset", email, 60) >= 3 or RateLimitHit.count("reset-ip", ip, 60) >= 15:
            return redirect(self.success_url)  # same response as success: no signal to an attacker
        RateLimitHit.hit("reset", email)
        RateLimitHit.hit("reset-ip", ip)
        return super().form_valid(form)


@admin_required
def team(request):
    members = User.objects.exclude(role=User.CUSTOMER).order_by("role", "name")
    return render(request, "accounts/team.html", {"members": members, "title": "Team"})


@admin_required
def team_form(request, pk=None):
    member = User.objects.exclude(role=User.CUSTOMER).get(pk=pk) if pk else None
    form = StaffForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        audit.log(request.user, "team.save", saved, f"role {saved.role}, active {saved.is_active}")
        messages.success(request, "Team member saved.")
        return redirect("accounts:team")
    return render(request, "accounts/team_form.html", {"form": form, "member": member, "title": "Team member"})


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
    return render(request, "accounts/password_change.html", {"form": form, "title": "Change password"})
