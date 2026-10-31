import fcntl
import time
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from billing.services import run_billing


class Command(BaseCommand):
    help = "Generate every due invoice (idempotent; catches up after downtime). Run from cron/systemd, or use --every to loop."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", type=date.fromisoformat, help="Bill as if today were YYYY-MM-DD (demo/testing).")
        parser.add_argument("--every", type=int, metavar="SECONDS", help="Stay alive and run repeatedly.")

    def handle(self, *a, as_of=None, every=None, **o):
        lock = open(settings.BASE_DIR / ".billing.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one billing process at a time
        except BlockingIOError:
            raise CommandError("Another billing run is in progress.")
        while True:
            r = run_billing(as_of, trigger="scheduler")
            self.stdout.write(f"created={r.created} skipped={r.skipped} failed={r.failed} closed={r.closed} renewed={r.renewed}")
            if r.errors:
                self.stderr.write(r.errors)
            if not every:
                return
            time.sleep(every)
