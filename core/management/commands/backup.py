import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Write a consistent snapshot of the SQLite database (safe while the app is running) and prune old ones."

    def add_arguments(self, p):
        p.add_argument("--to", default=str(settings.BASE_DIR / "backups"))
        p.add_argument("--keep", type=int, default=14)

    def handle(self, *a, to, keep, **o):
        db = settings.DATABASES["default"]
        if "sqlite" not in db["ENGINE"]:
            raise CommandError("backup supports SQLite only; use pg_dump for PostgreSQL.")
        dest = Path(to)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"brightloop-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        src = sqlite3.connect(db["NAME"])
        out = sqlite3.connect(target)
        with out:
            src.backup(out)  # online backup API: consistent even during writes
        out.close(); src.close()
        old = sorted(dest.glob("brightloop-*.sqlite3"))[:-keep]
        for f in old:
            f.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup written: {target} (pruned {len(old)})"))
