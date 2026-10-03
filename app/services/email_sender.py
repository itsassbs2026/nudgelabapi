"""Drains dash_email_outbox through Graph (the worker's email_sender job, every 15 s).

Retries after 1, 5 and 30 minutes, then gives up (dead). Does nothing while Graph isn't configured, so
emails wait in the outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.dashboard import DashEmailOutbox, OutboxStatus
from app.notifications.graph import GraphMailError, send_email

RETRY_BACKOFF_SECONDS = [60, 300, 1800]
MAX_ATTEMPTS = 4
BATCH_SIZE = 50


def attempt(settings: Settings, row: DashEmailOutbox) -> None:
    row.attempts += 1
    try:
        send_email(settings, row.to_email, row.subject, row.body_html)
    except GraphMailError as exc:
        row.last_error = str(exc)[:500]
        if row.attempts >= MAX_ATTEMPTS:
            row.status = OutboxStatus.DEAD.value
        else:
            row.status = OutboxStatus.FAILED.value
            backoff = RETRY_BACKOFF_SECONDS[min(row.attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff)
        return
    row.status = OutboxStatus.SENT.value
    row.sent_at = datetime.now(UTC)


def drain_email_outbox(db: Session, settings: Settings) -> int:
    """Returns how many emails were attempted."""
    if not settings.graph_configured:
        return 0
    rows = (
        db.execute(
            select(DashEmailOutbox)
            .where(
                DashEmailOutbox.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]),
                DashEmailOutbox.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(DashEmailOutbox.next_attempt_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for row in rows:
        attempt(settings, row)
    db.commit()
    return len(rows)
