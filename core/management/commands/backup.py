import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Write a consistent snapshot (SQLite online backup, or pg_dump for PostgreSQL) and prune old ones."

    def add_arguments(self, p):
        p.add_argument("--to", default=str(settings.BASE_DIR / "backups"))
        p.add_argument("--keep", type=int, default=14)

    def handle(self, *a, to, keep, **o):
        db = settings.DATABASES["default"]
        dest = Path(to)
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if "postgresql" in db["ENGINE"]:
            import os
            import subprocess
            target = dest / f"brightloop-{stamp}.dump"
            env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
            cmd = ["pg_dump", "-Fc", "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432), "-U", db.get("USER") or "postgres", "-f", str(target), db["NAME"]]
            try:
                subprocess.run(cmd, check=True, env=env, capture_output=True)
            except (OSError, subprocess.CalledProcessError) as e:
                raise CommandError(f"pg_dump failed: {getattr(e, 'stderr', b'').decode()[:300] or e}")
            pattern = "brightloop-*.dump"
        elif "sqlite" in db["ENGINE"]:
            target = dest / f"brightloop-{stamp}.sqlite3"
            src = sqlite3.connect(db["NAME"])
            out = sqlite3.connect(target)
            with out:
                src.backup(out)  # online backup API: consistent even during writes
            out.close(); src.close()
            pattern = "brightloop-*.sqlite3"
        else:
            raise CommandError("Unsupported database engine for backup.")
        old = sorted(dest.glob(pattern))[:-keep]
        for f in old:
            f.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup written: {target} (pruned {len(old)})"))
