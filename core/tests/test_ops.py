from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from billing import services as billing
from billing.models import Invoice
from core import automation, mail as outbox
from core.models import AutomationLog, AutomationRule, EmailTemplate, Notification, Outbox, Task
from subscriptions import services as subs
from subscriptions.models import Subscription, add_interval

from .helpers import Base, make_active, make_world


class OutboxTests(Base):
    def test_delivery_is_claimed_before_smtp_so_another_worker_cannot_send_it(self):
        row = outbox.queue_email("a@x.test", "S", "B", deliver=False)
        competing = []
        def smtp_send(*args, **kwargs):
            competing.append(outbox.deliver_one(row.pk))
            return 1
        with mock.patch("core.mail.EmailMessage.send", side_effect=smtp_send) as send:
            self.assertTrue(outbox.deliver_one(row.pk))
        self.assertEqual(competing, [False])
        self.assertEqual(send.call_count, 1)

    def test_interrupted_delivery_requires_explicit_review_after_the_delivery_window(self):
        row = outbox.queue_email("a@x.test", "S", "B", deliver=False)
        Outbox.objects.filter(pk=row.pk).update(status="sending", next_attempt_at=timezone.now() + timedelta(minutes=10))
        self.assertEqual(outbox.flush(), (0, 0))
        self.assertFalse(outbox.retry(row.pk, reviewed=True))
        Outbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(outbox.retry(row.pk))
        self.assertTrue(outbox.retry(row.pk, reviewed=True))

    def test_dedupe_key_prevents_duplicate_queueing(self):
        a = outbox.queue_email("a@x.test", "S", "B", dedupe_key="k")
        b = outbox.queue_email("a@x.test", "S", "B", dedupe_key="k")
        self.assertIsNotNone(a)
        self.assertIsNone(b)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_delivery_failure_keeps_row_and_backs_off_then_recovers(self):
        with mock.patch("core.mail.EmailMessage.send", side_effect=OSError("smtp down")), self.captureOnCommitCallbacks(execute=True):
            row = outbox.queue_email("a@x.test", "S", "B", deliver=True)
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), ("pending", 1))
        self.assertGreater(row.next_attempt_at, timezone.now())
        self.assertEqual(outbox.flush(), (0, 0))  # backoff: nothing due yet
        Outbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())
        self.assertEqual(outbox.flush(), (1, 0))
        row.refresh_from_db()
        self.assertEqual((row.status, len(mail.outbox)), ("sent", 1))

    def test_gives_up_after_max_attempts_and_can_be_retried(self):
        row = outbox.queue_email("a@x.test", "S", "B", deliver=False)
        with mock.patch("core.mail.EmailMessage.send", side_effect=OSError("down")):
            for _ in range(outbox.MAX_ATTEMPTS):
                outbox.deliver_one(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.status, "failed")
        self.assertTrue(outbox.retry(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.status, "sent")

    def test_billing_still_works_when_mail_is_down(self):
        make_active(self.plan, self.cust, self.today)
        with mock.patch("core.mail.EmailMessage.send", side_effect=OSError("down")), self.captureOnCommitCallbacks(execute=True):
            r = billing.run_billing(self.today)
            billing.send_invoice(Invoice.objects.get())
        self.assertEqual((r.created, r.failed), (1, 0))
        self.assertTrue(Outbox.objects.filter(status="pending").exists())

    def test_email_is_not_sent_if_the_transaction_rolls_back(self):
        from django.db import transaction
        try:
            with transaction.atomic():
                outbox.queue_email("a@x.test", "S", "B", dedupe_key="rb")
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        self.assertEqual((Outbox.objects.count(), len(mail.outbox)), (0, 0))


class AutomationTests(Base):
    def test_defaults_exist_and_activation_sends_welcome_once(self):
        self.assertTrue(AutomationRule.objects.filter(trigger="subscription_activated").exists())
        with self.captureOnCommitCallbacks(execute=True):
            s = make_active(self.plan, self.cust, self.today)
        outbox.flush()
        self.assertEqual(sum("is active" in m.subject for m in mail.outbox), 1)
        self.assertEqual(automation.fire("subscription_activated", s), 0)  # same record + bucket never runs twice

    def test_overdue_rule_repeats_on_schedule_not_on_every_run(self):
        make_active(self.plan, self.cust, self.today - timedelta(days=60))
        billing.run_billing(self.today)
        for i in Invoice.objects.all():
            billing.confirm_invoice(i)
        Invoice.objects.update(due_date=self.today - timedelta(days=10))
        first = automation.run_scheduled(self.today)["invoice_overdue"]
        self.assertEqual(first, Invoice.objects.count())
        self.assertEqual(automation.run_scheduled(self.today)["invoice_overdue"], 0)
        later = automation.run_scheduled(self.today + timedelta(days=7))["invoice_overdue"]  # next 7-day bucket
        self.assertEqual(later, Invoice.objects.count())

    def test_renewal_notice_and_quotation_expiry_rules(self):
        s = make_active(self.plan, self.cust, self.today)
        Subscription.objects.filter(pk=s.pk).update(expiry_date=self.today + timedelta(days=5))
        out = automation.run_scheduled(self.today)
        self.assertEqual(out["renewal_upcoming"], 1)
        q = subs.create_subscription(customer=self.cust, plan=self.plan)
        subs.send_quotation(q)
        Subscription.objects.filter(pk=q.pk).update(quote_valid_until=self.today + timedelta(days=2))
        self.assertEqual(automation.run_scheduled(self.today)["quotation_expiring"], 1)

    def test_notify_and_task_actions_and_disabled_rules(self):
        s = make_active(self.plan, self.cust, self.today)
        subs.close(Subscription.objects.get(pk=s.pk), reason="other", immediate=True)
        self.assertTrue(Task.objects.filter(title__contains="Follow up").exists())
        AutomationRule.objects.update(enabled=False)
        before = AutomationLog.objects.count()
        make_active(*make_world(), start=self.today)
        self.assertEqual(AutomationLog.objects.count(), before)

    def test_manual_reminder_uses_template_and_counts(self):
        make_active(self.plan, self.cust, self.today - timedelta(days=40))
        billing.run_billing(self.today)
        inv = Invoice.objects.order_by("period_start").first()
        billing.confirm_invoice(inv)
        Invoice.objects.filter(pk=inv.pk).update(due_date=self.today - timedelta(days=3))
        inv.refresh_from_db()
        billing.send_reminder(inv, self.u["admin"])
        inv.refresh_from_db()
        self.assertEqual(inv.reminder_count, 1)
        self.assertTrue(Outbox.objects.filter(subject__contains="overdue").exists())
        billing.confirm_invoice(Invoice.objects.exclude(pk=inv.pk).first()) if Invoice.objects.exclude(pk=inv.pk).filter(state="draft").exists() else None
        with self.assertRaises(billing.BillingError):
            billing.send_reminder(Invoice.objects.exclude(pk=inv.pk).first(), self.u["admin"])  # not overdue


class Commands(Base):
    def test_performance_check_explains_missing_seed_data(self):
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, "Run seed_demo first"):
            call_command("perf_check", stdout=mock.Mock())

    def test_performance_check_rejects_empty_measurements(self):
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, "between 1 and 100"):
            call_command("perf_check", runs=0, stdout=mock.Mock())

    def test_run_scheduled_does_billing_automation_and_outbox_in_order(self):
        make_active(self.plan, self.cust, self.today - timedelta(days=5))
        call_command("run_scheduled", stdout=mock.Mock())
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(Outbox.objects.filter(status="pending").count(), 0)

    @__import__('unittest').skipUnless(connection.vendor == 'sqlite', 'SQLite backup path; PostgreSQL uses pg_dump')
    def test_backup_writes_a_restorable_copy(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "live.sqlite3"
            con = sqlite3.connect(src)
            con.execute("create table t (x)")
            con.execute("insert into t values (42)")
            con.commit()
            con.close()
            dbs = {"default": {**__import__("django.conf", fromlist=["settings"]).settings.DATABASES["default"], "NAME": str(src)}}
            with override_settings(DATABASES=dbs):
                call_command("backup", to=str(Path(d) / "bk"), keep=2, stdout=mock.Mock())
            files = list((Path(d) / "bk").glob("*.sqlite3"))
            self.assertEqual(len(files), 1)
            self.assertEqual(sqlite3.connect(files[0]).execute("select x from t").fetchone()[0], 42)

    def test_healthz(self):
        self.assertEqual(self.client.get(reverse("core:healthz")).json(), {"status": "ok"})


class OutboxPage(Base):
    def test_test_email_button_delivers_to_the_admin_and_is_admin_only(self):
        self.client.force_login(self.u["admin"])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("core:outbox"), {"test": "1"})
        self.assertTrue(any(m.to == ["admin@x.test"] for m in mail.outbox))
        self.client.force_login(self.u["sales"])
        self.assertEqual(self.client.post(reverse("core:outbox"), {"test": "1"}).status_code, 403)
