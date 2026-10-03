"""Deletes refresh and password-reset tokens that can no longer be used (worker job, every 6 hours)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.dashboard import DashPasswordResetToken, DashRefreshToken

# Kept a while after expiry so a reused (stolen) refresh token is still recognised and revokes its family.
GRACE_DAYS = 8


def purge_expired_tokens(db: Session, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=GRACE_DAYS)
    refresh = db.execute(delete(DashRefreshToken).where(DashRefreshToken.expires_at < cutoff))
    resets = db.execute(
        delete(DashPasswordResetToken).where(
            or_(DashPasswordResetToken.expires_at < cutoff, DashPasswordResetToken.used_at < cutoff)
        )
    )
    db.commit()
    return int(refresh.rowcount or 0) + int(resets.rowcount or 0)  # type: ignore[attr-defined]
