from .models import AuditLog


def log(actor, action, target=None, detail=""):
    user = actor if getattr(actor, "pk", None) else None
    AuditLog.objects.create(
        actor=user, actor_label=(str(actor) if actor else "system"), action=action,
        target_type=type(target).__name__ if target is not None else "", target_id=getattr(target, "pk", None),
        target_label=str(target)[:200] if target is not None else "", detail=detail[:500])


def timeline(target, limit=12):
    from .models import AuditLog
    return AuditLog.objects.filter(target_type=type(target).__name__, target_id=target.pk)[:limit]
