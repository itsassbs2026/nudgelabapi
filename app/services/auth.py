"""Sign-in and password logic (SPEC §8.1, §9).

No FastAPI here: routers turn results into responses and cookies. Lockout: 5 failed logins lock the
account for 15 minutes. Errors never reveal whether an email exists.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import refresh as refresh_tokens
from app.auth.jwt import create_access_token
from app.auth.passwords import hash_password, password_policy_violation, verify_password
from app.auth.refresh import RefreshTokenError, RefreshTokenReused
from app.config import Settings
from app.models.dashboard import DashEmailOutbox, DashPasswordResetToken, DashUser, OutboxStatus
from app.notifications.render import render_html
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

FAILED_LOGIN_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def _invalid_credentials() -> ApiError:
    # The same answer for a wrong password, an unknown or inactive email and a locked account, so a guesser
    # can't learn which emails have accounts; the message still tells a real user about the lockout.
    return ApiError(
        401,
        "invalid_credentials",
        f"Incorrect email or password. After {FAILED_LOGIN_THRESHOLD} failed tries, sign-in is locked for "
        f"{LOCKOUT_MINUTES} minutes.",
    )


def _invalid_refresh() -> ApiError:
    return ApiError(401, "invalid_refresh_token", "Refresh token invalid or expired.")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    user: DashUser
    access_token: str
    expires_in: int
    refresh_token: str


@dataclass(frozen=True)
class RefreshResult:
    access_token: str
    expires_in: int
    refresh_token: str


def find_user_by_email(db: Session, email: str) -> DashUser | None:
    return db.execute(select(DashUser).filter_by(email=email.strip().lower())).scalar_one_or_none()


def login(
    db: Session, settings: Settings, email: str, password: str, user_agent: str | None, ip: str | None
) -> LoginResult:
    user = find_user_by_email(db, email)
    now = datetime.now(UTC)

    if user is not None and user.locked_until is not None and _utc(user.locked_until) > now:
        audit.record(db, AuditAction.LOGIN_FAILED, actor_user_id=user.id, details={"reason": "locked"}, ip=ip)
        db.commit()
        raise _invalid_credentials()

    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        if user is not None and user.is_active:
            user.failed_login_count += 1
            locked = user.failed_login_count >= FAILED_LOGIN_THRESHOLD
            if locked:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            audit.record(
                db,
                AuditAction.ACCOUNT_LOCKED if locked else AuditAction.LOGIN_FAILED,
                actor_user_id=user.id,
                ip=ip,
            )
        else:
            # Unknown or inactive email: recorded without an actor, and without the email text itself.
            audit.record(
                db,
                AuditAction.LOGIN_FAILED,
                actor_user_id=user.id if user else None,
                details={"reason": "inactive" if user else "unknown_email"},
                ip=ip,
            )
        db.commit()
        raise _invalid_credentials()

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    access_token, expires_in = create_access_token(user, settings)
    refresh_plain, _ = refresh_tokens.issue_new_family(db, user.id, settings, user_agent, ip)
    audit.record(db, AuditAction.LOGIN, actor_user_id=user.id, ip=ip)
    db.commit()
    return LoginResult(
        user=user, access_token=access_token, expires_in=expires_in, refresh_token=refresh_plain
    )


def refresh(
    db: Session, settings: Settings, cookie: str | None, user_agent: str | None, ip: str | None
) -> RefreshResult:
    if not cookie:
        raise _invalid_refresh()
    try:
        new_plain, new_row = refresh_tokens.rotate(db, cookie, settings, user_agent, ip)
    except RefreshTokenReused as exc:
        audit.record(db, AuditAction.REFRESH_TOKEN_REUSED, actor_user_id=None, ip=ip)
        db.commit()
        raise _invalid_refresh() from exc
    except RefreshTokenError as exc:
        db.commit()
        raise _invalid_refresh() from exc

    user = db.get(DashUser, new_row.user_id)
    if user is None or not user.is_active:
        refresh_tokens.revoke_all_for_user(db, new_row.user_id)
        db.commit()
        raise _invalid_refresh()
    access_token, expires_in = create_access_token(user, settings)
    db.commit()
    return RefreshResult(access_token=access_token, expires_in=expires_in, refresh_token=new_plain)


def logout(db: Session, user: DashUser, cookie: str | None, ip: str | None) -> None:
    if cookie:
        refresh_tokens.revoke_family_by_token(db, cookie)
    audit.record(db, AuditAction.LOGOUT, actor_user_id=user.id, ip=ip)
    db.commit()


def check_password_policy(password: str) -> None:
    violation = password_policy_violation(password)
    if violation:
        raise ApiError(422, "weak_password", violation)


def change_password(
    db: Session, user: DashUser, current_password: str, new_password: str, ip: str | None
) -> None:
    if not verify_password(current_password, user.password_hash):
        # 400, not 401: a 401 means "your session is gone" to the dashboard, which would sign the user out.
        raise ApiError(400, "invalid_current_password", "Current password is incorrect.")
    check_password_policy(new_password)
    if verify_password(new_password, user.password_hash):
        raise ApiError(422, "same_password", "Choose a password different from the current one.")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    audit.record(
        db,
        AuditAction.PASSWORD_CHANGED,
        actor_user_id=user.id,
        target_type="dash_user",
        target_id=user.id,
        ip=ip,
    )
    db.commit()


def enqueue_password_reset_email(
    db: Session, settings: Settings, user: DashUser, *, invited: bool = False
) -> str:
    """A one-time reset token plus an outbox email with the link. Used by "forgot password", by an Admin's
    "send reset email", and when an Admin creates a user without a temporary password. Doesn't commit.
    Returns the link (used only by tests and the local-dev script)."""
    plain = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        DashPasswordResetToken(
            user_id=user.id,
            token_hash=_hash(plain),
            expires_at=now + timedelta(minutes=settings.password_reset_token_minutes),
        )
    )
    link = f"{settings.dashboard_base_url}/reset-password?token={plain}"
    db.add(
        DashEmailOutbox(
            to_email=user.email,
            subject="Set your NudgeLab dashboard password"
            if invited
            else "Reset your NudgeLab dashboard password",
            body_html=render_html(
                "password_reset",
                reset_link=link,
                invited=invited,
                expires_minutes=settings.password_reset_token_minutes,
            ),
            template="password_reset",
            status=OutboxStatus.PENDING.value,
            next_attempt_at=now,
        )
    )
    return link


def forgot_password(db: Session, settings: Settings, email: str, ip: str | None) -> None:
    """Always "done" from the caller's side (no account enumeration); acts only for an active user."""
    user = find_user_by_email(db, email)
    if user is None or not user.is_active:
        return
    enqueue_password_reset_email(db, settings, user)
    audit.record(
        db,
        AuditAction.PASSWORD_RESET_REQUESTED,
        actor_user_id=user.id,
        target_type="dash_user",
        target_id=user.id,
        ip=ip,
    )
    db.commit()


def reset_password(db: Session, token_plain: str, new_password: str, ip: str | None) -> None:
    now = datetime.now(UTC)
    row = db.execute(
        select(DashPasswordResetToken).filter_by(token_hash=_hash(token_plain))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or _utc(row.expires_at) <= now:
        raise ApiError(400, "invalid_or_expired_token", "Reset link is invalid or has expired.")
    check_password_policy(new_password)
    user = db.get(DashUser, row.user_id)
    if user is None or not user.is_active:
        raise ApiError(400, "invalid_or_expired_token", "Reset link is invalid or has expired.")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    row.used_at = now
    refresh_tokens.revoke_all_for_user(db, user.id)
    audit.record(
        db,
        AuditAction.PASSWORD_RESET,
        actor_user_id=user.id,
        target_type="dash_user",
        target_id=user.id,
        ip=ip,
    )
    db.commit()
