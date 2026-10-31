from django import forms as djforms
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from billing import providers

from . import audit, mail
from .crud import StyledModelForm, paginate
from .models import AuditLog, AutomationRule, CompanySettings, EmailTemplate, Outbox
from .permissions import admin_required


class CompanyForm(StyledModelForm):
    class Meta:
        model = CompanySettings
        exclude = []
        widgets = {"address": djforms.Textarea(attrs={"rows": 3}), "bank_details": djforms.Textarea(attrs={"rows": 3})}


@admin_required
def company_settings(request):
    form = CompanyForm(request.POST or None, instance=CompanySettings.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request.user, "settings.update", detail="company settings")
        messages.success(request, "Settings saved.")
        return redirect("core:settings")
    return render(request, "core/settings.html", {"form": form, "title": "Settings", "help": ["Company details appear on every invoice, quotation and statement.", "Terms and conditions print on each document, one point per line.", "Auto-confirm and auto-email decide what billing runs do with new invoices.", "Reminder timing feeds the automation rules."]})


@admin_required
def payments_settings(request):
    from django.conf import settings
    import os
    return render(request, "core/settings_payments.html", {
        "configured": providers.configured(), "webhook": providers.webhook_configured(), "test_mode": providers.is_test_mode(),
        "webhook_url": settings.SITE_URL + "/billing/webhooks/razorpay/", "site_url": settings.SITE_URL, "title": "Payments"})


class RuleForm(StyledModelForm):
    class Meta:
        model = AutomationRule
        fields = ["name", "trigger", "action", "template", "offset_days", "repeat_days", "enabled"]


class TemplateForm(StyledModelForm):
    class Meta:
        model = EmailTemplate
        fields = ["name", "subject", "body"]
        widgets = {"body": djforms.Textarea(attrs={"rows": 9})}


@admin_required
def automation(request):
    return render(request, "core/automation.html", {"rules": AutomationRule.objects.select_related("template"), "templates": EmailTemplate.objects.all(), "title": "Automation"})


@admin_required
def automation_rule(request, pk=None):
    obj = get_object_or_404(AutomationRule, pk=pk) if pk else None
    form = RuleForm(request.POST or None, instance=obj)
    if request.method == "POST" and request.POST.get("delete") and obj:
        obj.delete()
        messages.success(request, "Rule deleted.")
        return redirect("core:automation")
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        audit.log(request.user, "automation.save", saved)
        messages.success(request, "Rule saved.")
        return redirect("core:automation")
    return render(request, "core/automation_rule.html", {"form": form, "obj": obj, "title": "Automation rule", "help": ["A rule runs at most once per record and period, so re-runs never duplicate emails.", "Scheduled triggers use the number of days before (renewal, due, expiry) or after (overdue).", "Disable a rule instead of deleting it to keep its history."]})


@admin_required
def template_edit(request, pk):
    obj = get_object_or_404(EmailTemplate, pk=pk)
    form = TemplateForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request.user, "template.save", obj)
        messages.success(request, "Template saved.")
        return redirect("core:automation")
    return render(request, "core/template_edit.html", {"form": form, "obj": obj, "title": f"Template · {obj.name}", "help": ["Variables in braces are replaced per customer: {customer} {number} {amount} {due_date} {plan} {company} {link}.", "Plain text only. Keep subjects short and specific."]})


@admin_required
def outbox(request):
    if request.method == "POST":
        if request.POST.get("test"):
            import uuid
            row = mail.queue_email(request.user.email, "Brightloop test email", "If you can read this, outgoing email works.", dedupe_key=f"test:{uuid.uuid4().hex}")
            row.refresh_from_db()
            messages.success(request, f"Test email sent to {request.user.email}." if row.status == "sent" else f"Test email queued but not delivered yet: {row.last_error or 'see the outbox below'}")
        elif request.POST.get("flush"):
            sent, failed = mail.flush()
            messages.success(request, f"Delivered {sent}; {failed} still failing.")
        elif request.POST.get("retry"):
            ok = mail.retry(int(request.POST["retry"]))
            messages.success(request, "Delivered." if ok else "Still failing; the error is shown on the row.")
        return redirect("core:outbox")
    status = request.GET.get("status", "")
    qs = Outbox.objects.all()
    if status:
        qs = qs.filter(status=status)
    counts = {k: Outbox.objects.filter(status=k).count() for k in ("pending", "sent", "failed")}
    return render(request, "core/outbox.html", {"page": paginate(request, qs), "counts": counts, "status": status, "title": "Email outbox"})


@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related("actor")
    q, action = request.GET.get("q", "").strip(), request.GET.get("action", "")
    if q:
        qs = qs.filter(Q(target_label__icontains=q) | Q(actor_label__icontains=q) | Q(detail__icontains=q))
    if action:
        qs = qs.filter(action__startswith=action)
    groups = ["subscription", "invoice", "payment", "billing", "account", "team", "settings", "portal", "customer", "automation"]
    return render(request, "core/audit.html", {"page": paginate(request, qs), "q": q, "action": action, "groups": groups, "title": "Audit log"})
