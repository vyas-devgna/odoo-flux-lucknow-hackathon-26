"""Transactional email outbox. Messages are recorded first, delivered second, and retried with backoff."""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from .models import CompanySettings, Outbox

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 6


def queue_email(to, subject, body, *, dedupe_key=None, related=None, reply_to=None, deliver=True):
    """Record an email and try to send it. Returns the Outbox row, or None if `dedupe_key` was already queued.
    Delivery happens after the surrounding transaction commits, so a rollback never sends a stale message."""
    co = CompanySettings.get()
    try:
        with transaction.atomic():
            row = Outbox.objects.create(
                to=to, subject=subject[:250], body=body, reply_to=reply_to or co.email, dedupe_key=dedupe_key,
                related_type=type(related).__name__ if related is not None else "", related_id=getattr(related, "pk", None))
    except IntegrityError:
        return None
    if deliver:
        if connection.in_atomic_block:
            transaction.on_commit(lambda: deliver_one(row.pk))
        else:
            deliver_one(row.pk)
    return row


def deliver_one(pk):
    claimed = Outbox.objects.filter(pk=pk, status=Outbox.PENDING).update(
        status=Outbox.SENDING, next_attempt_at=timezone.now() + timedelta(minutes=10))
    if not claimed:
        return False
    row = Outbox.objects.get(pk=pk)
    try:
        EmailMessage(row.subject, row.body, to=[row.to], reply_to=[row.reply_to] if row.reply_to else None).send(fail_silently=False)
    except Exception as e:  # network/SMTP failure: keep the row, back off
        row.attempts += 1
        row.last_error = str(e)[:300]
        row.status = Outbox.PENDING
        if row.attempts >= MAX_ATTEMPTS:
            row.status = Outbox.FAILED
        else:
            row.next_attempt_at = timezone.now() + timedelta(minutes=min(5 * 2 ** row.attempts, 720))
        row.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])
        log.warning("email to %s failed (attempt %s): %s", row.to, row.attempts, row.last_error)
        return False
    row.status, row.sent_at, row.last_error = Outbox.SENT, timezone.now(), ""
    row.save(update_fields=["status", "sent_at", "last_error"])
    return True


def flush(limit=200):
    """Send everything due. Run by the scheduler; safe to run concurrently with the web app."""
    sent = failed = 0
    due = Outbox.objects.filter(status=Outbox.PENDING, next_attempt_at__lte=timezone.now()).order_by("id")[:limit]
    for row in list(due):
        if deliver_one(row.pk):
            sent += 1
        else:
            failed += 1
    return sent, failed


def retry(pk, *, reviewed=False):
    rows = Outbox.objects.filter(pk=pk, status=Outbox.FAILED)
    if reviewed:
        rows = Outbox.objects.filter(pk=pk, status=Outbox.SENDING, next_attempt_at__lte=timezone.now())
    rows.update(status=Outbox.PENDING, attempts=0, next_attempt_at=timezone.now())
    return deliver_one(pk)
