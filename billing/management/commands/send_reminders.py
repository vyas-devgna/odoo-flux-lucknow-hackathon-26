from django.core.management.base import BaseCommand

from billing.services import send_due_reminders


class Command(BaseCommand):
    help = "Email overdue-invoice reminders and upcoming-expiry notices (safe to run daily)."

    def handle(self, *a, **o):
        sent, notices, failed = send_due_reminders()
        self.stdout.write(f"reminders={sent} renewal_notices={notices} failed={failed}")
