import fcntl
import time
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from billing.services import run_billing
from core import automation, mail


class Command(BaseCommand):
    help = "The one scheduler entry point: billing run, then automation rules, then the email outbox. Safe to run every few minutes."

    def add_arguments(self, p):
        p.add_argument("--as-of", type=date.fromisoformat)
        p.add_argument("--every", type=int, metavar="SECONDS")

    def handle(self, *a, as_of=None, every=None, **o):
        lock = open(settings.BASE_DIR / ".scheduled.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError("Another scheduled run is in progress.")
        while True:
            r = run_billing(as_of, trigger="scheduler")
            auto = automation.run_scheduled(as_of)
            sent, failed = mail.flush()
            self.stdout.write(f"billing: created={r.created} skipped={r.skipped} failed={r.failed} closed={r.closed} renewed={r.renewed} | automation={auto} | email: sent={sent} failed={failed}")
            if not every:
                return
            time.sleep(every)
