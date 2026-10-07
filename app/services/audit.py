"""The audit log (SPEC §4, §12): who did what, to what, from where. Append-only. Callers commit."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.models.dashboard import DashAuditLog


class AuditAction(StrEnum):
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    ACCOUNT_LOCKED = "account_locked"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET = "password_reset"
    REFRESH_TOKEN_REUSED = "refresh_token_reused"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    ADMIN_PASSWORD_RESET = "admin_password_reset"
    TRANSCRIPT_VIEWED = "transcript_viewed"
    RECORDING_PLAYED = "recording_played"
    QUALITY_UPDATED = "quality_updated"
    EXPORT = "export"
    TRAINING_CREATED = "training_created"
    TRAINING_UPDATED = "training_updated"
    VERSION_CREATED = "version_created"
    UPLOAD_STARTED = "upload_started"
    SETTINGS_CHANGED = "settings_changed"  # voices and setups (Phase 14)
    TESTER_CHANGED = "tester_changed"
    PREVIEW_CALL = "preview_call"  # a preview call was started (Phase 15)
    VERSION_SUBMITTED = "version_submitted"  # Phase 16
    VERSION_SENT_BACK = "version_sent_back"
    TRAINING_PUBLISHED = "training_published"
    REFERENCE_SYNC_CODE_SENT = "reference_sync_code_sent"  # 2026-10-06: the manual Portal sync
    REFERENCE_SYNC_CODE_FAILED = "reference_sync_code_failed"
    REFERENCE_SYNC_STARTED = "reference_sync_started"
    # Later phases: training_published, settings_changed, …


def record(
    db: Session,
    action: AuditAction,
    *,
    actor_user_id: int | None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        DashAuditLog(
            actor_user_id=actor_user_id,
            action=action.value,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            details=details,
            ip=ip,
        )
    )
