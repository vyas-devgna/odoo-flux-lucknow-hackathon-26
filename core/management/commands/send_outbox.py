from django.core.management.base import BaseCommand

from core import mail


class Command(BaseCommand):
    help = "Deliver pending outbox emails now (retries with backoff)."

    def handle(self, *a, **o):
        sent, failed = mail.flush()
        self.stdout.write(f"sent={sent} failed={failed}")
