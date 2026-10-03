"""Refresh tokens (SPEC §9): opaque 256-bit, stored as SHA-256.

Each token lives 12 hours (sliding), and a family lives at most 7 days from its first login. Each refresh
revokes the old token and issues a new one; presenting a revoked token again (reuse) revokes the whole
family and forces a new login."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.dashboard import DashRefreshToken


class RefreshTokenError(Exception):
    """Any refresh failure: the caller must log in again."""


class RefreshTokenInvalid(RefreshTokenError):
    pass


class RefreshTokenExpired(RefreshTokenError):
    pass


class RefreshTokenReused(RefreshTokenError):
    """A revoked token was presented again; the whole family has been revoked."""


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _new_row(
    db: Session, user_id: int, family_id: str, settings: Settings, user_agent: str | None, ip: str | None
) -> tuple[str, DashRefreshToken]:
    plain = secrets.token_hex(32)  # 256 bits
    row = DashRefreshToken(
        user_id=user_id,
        token_hash=_hash(plain),
        family_id=family_id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.refresh_token_hours),
        user_agent=(user_agent or "")[:255] or None,
        ip=ip,
    )
    db.add(row)
    db.flush()
    return plain, row


def issue_new_family(
    db: Session, user_id: int, settings: Settings, user_agent: str | None, ip: str | None
) -> tuple[str, DashRefreshToken]:
    """A fresh token family, at login."""
    return _new_row(db, user_id, uuid.uuid4().hex, settings, user_agent, ip)


def rotate(
    db: Session, plain_token: str, settings: Settings, user_agent: str | None, ip: str | None
) -> tuple[str, DashRefreshToken]:
    row = db.execute(select(DashRefreshToken).filter_by(token_hash=_hash(plain_token))).scalar_one_or_none()
    if row is None:
        raise RefreshTokenInvalid("unknown refresh token")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        revoke_family(db, row.family_id)
        raise RefreshTokenReused("refresh token reuse detected")

    started = db.execute(
        select(func.min(DashRefreshToken.created_at)).filter_by(family_id=row.family_id)
    ).scalar_one()
    absolute_deadline = _utc(started) + timedelta(days=settings.refresh_token_max_days)
    if now >= _utc(row.expires_at) or now >= absolute_deadline:
        row.revoked_at = now
        db.flush()
        raise RefreshTokenExpired("refresh token expired")

    new_plain, new_row = _new_row(db, row.user_id, row.family_id, settings, user_agent, ip)
    row.revoked_at = now
    row.replaced_by_id = new_row.id
    db.flush()
    return new_plain, new_row


def revoke_family(db: Session, family_id: str) -> None:
    db.execute(
        update(DashRefreshToken)
        .where(DashRefreshToken.family_id == family_id, DashRefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()


def revoke_family_by_token(db: Session, plain_token: str) -> None:
    """Logout: revokes the token's family if it's known; does nothing otherwise."""
    row = db.execute(select(DashRefreshToken).filter_by(token_hash=_hash(plain_token))).scalar_one_or_none()
    if row is not None:
        revoke_family(db, row.family_id)


def revoke_all_for_user(db: Session, user_id: int) -> None:
    """Deactivation, password change or reset: every session of this user ends."""
    db.execute(
        update(DashRefreshToken)
        .where(DashRefreshToken.user_id == user_id, DashRefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
