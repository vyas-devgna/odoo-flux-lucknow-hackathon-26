from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from .defaults import ensure_defaults
        post_migrate.connect(lambda **kw: ensure_defaults() if kw.get("app_config") and kw["app_config"].name == "core" else None, weak=False)
