"""Dashboard user management (SPEC §4: Admin only). No FastAPI here.

Safeguards: there is always at least one active Admin, and an Admin can't demote or deactivate
themselves, so nobody locks the dashboard by accident. Deactivating a user, or resetting their password,
ends their sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import refresh as refresh_tokens
from app.auth.passwords import hash_password
from app.config import Settings
from app.models.dashboard import DashRole, DashUser
from app.services import audit
from app.services.audit import AuditAction
from app.services.auth import check_password_policy, enqueue_password_reset_email, find_user_by_email
from app.utils.errors import ApiError


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "User not found.")


def _check_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(422, "invalid_timezone", f"Unknown time zone: {tz}.") from exc


def _active_admin_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(DashUser).filter_by(role=DashRole.ADMIN.value, is_active=True)
        ).scalar_one()
    )


@dataclass(frozen=True)
class UserPage:
    items: list[DashUser]
    total: int


def list_users(
    db: Session, *, search: str | None, role: str | None, is_active: bool | None, page: int, page_size: int
) -> UserPage:
    query = select(DashUser)
    if search:
        like = f"%{search.strip().lower()}%"
        query = query.where(
            or_(func.lower(DashUser.email).like(like), func.lower(DashUser.full_name).like(like))
        )
    if role:
        query = query.where(DashUser.role == role)
    if is_active is not None:
        query = query.where(DashUser.is_active == is_active)
    total = int(db.execute(select(func.count()).select_from(query.subquery())).scalar_one())
    items = (
        db.execute(
            query.order_by(DashUser.full_name, DashUser.id).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return UserPage(items=list(items), total=total)


def get_user(db: Session, user_id: int) -> DashUser:
    user = db.get(DashUser, user_id)
    if user is None:
        raise _not_found()
    return user


def create_user(
    db: Session,
    settings: Settings,
    actor: DashUser,
    *,
    email: str,
    full_name: str,
    role: DashRole,
    timezone: str | None,
    temporary_password: str | None,
    ip: str | None,
) -> DashUser:
    """With a temporary password: the user must change it at first sign-in. Without one: an invitation email
    with a set-your-password link (needs Graph; it waits in the outbox until Graph is configured)."""
    normalized = email.strip().lower()
    if find_user_by_email(db, normalized) is not None:
        raise ApiError(409, "email_taken", "A user with this email already exists.")
    tz = timezone or settings.default_timezone
    _check_timezone(tz)
    if temporary_password is not None:
        check_password_policy(temporary_password)
    user = DashUser(
        email=normalized,
        full_name=full_name.strip(),
        role=role.value,
        timezone=tz,
        is_active=True,
        must_change_password=True,
        # No temporary password: an unguessable placeholder until the invited user sets their own.
        password_hash=hash_password(temporary_password or _unusable_secret()),
        preferences={},
    )
    db.add(user)
    db.flush()
    if temporary_password is None:
        enqueue_password_reset_email(db, settings, user, invited=True)
    audit.record(
        db,
        AuditAction.USER_CREATED,
        actor_user_id=actor.id,
        target_type="dash_user",
        target_id=user.id,
        details={"role": role.value, "invited_by_email": temporary_password is None},
        ip=ip,
    )
    db.commit()
    return user


def _unusable_secret() -> str:
    import secrets

    return secrets.token_urlsafe(48)


def update_user(
    db: Session, actor: DashUser, user_id: int, changes: dict[str, Any], ip: str | None
) -> DashUser:
    user = get_user(db, user_id)
    is_self = user.id == actor.id
    demoting = "role" in changes and changes["role"] != user.role and user.role == DashRole.ADMIN.value
    deactivating = changes.get("is_active") is False and user.is_active

    if is_self and (demoting or deactivating):
        raise ApiError(
            409, "cannot_change_own_access", "You can't remove your own Admin access or deactivate yourself."
        )
    if (demoting or deactivating) and user.role == DashRole.ADMIN.value and _active_admin_count(db) <= 1:
        raise ApiError(409, "last_admin", "There must always be at least one active Admin.")
    if "timezone" in changes:
        _check_timezone(changes["timezone"])

    before = {k: getattr(user, k) for k in changes}
    for key, value in changes.items():
        setattr(user, key, value.value if isinstance(value, DashRole) else value)
    if "full_name" in changes:
        user.full_name = user.full_name.strip()

    if deactivating:
        refresh_tokens.revoke_all_for_user(db, user.id)
        action = AuditAction.USER_DEACTIVATED
    elif changes.get("is_active") is True and not before.get("is_active", True):
        user.failed_login_count = 0
        user.locked_until = None
        action = AuditAction.USER_REACTIVATED
    else:
        action = AuditAction.USER_UPDATED
    audit.record(
        db,
        action,
        actor_user_id=actor.id,
        target_type="dash_user",
        target_id=user.id,
        details={"changed": sorted(changes)},
        ip=ip,
    )
    db.commit()
    return user


def admin_reset_password(
    db: Session,
    settings: Settings,
    actor: DashUser,
    user_id: int,
    *,
    temporary_password: str | None,
    ip: str | None,
) -> None:
    """Either set a temporary password (changed at next sign-in) or email a reset link. Both end the user's
    current sessions and unlock the account."""
    user = get_user(db, user_id)
    if not user.is_active:
        raise ApiError(409, "user_inactive", "Reactivate the user before resetting their password.")
    if temporary_password is not None:
        check_password_policy(temporary_password)
        user.password_hash = hash_password(temporary_password)
        user.must_change_password = True
        mode = "temporary_password"
    else:
        enqueue_password_reset_email(db, settings, user)
        mode = "email"
    user.failed_login_count = 0
    user.locked_until = None
    refresh_tokens.revoke_all_for_user(db, user.id)
    audit.record(
        db,
        AuditAction.ADMIN_PASSWORD_RESET,
        actor_user_id=actor.id,
        target_type="dash_user",
        target_id=user.id,
        details={"mode": mode},
        ip=ip,
    )
    db.commit()
