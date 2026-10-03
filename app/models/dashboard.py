"""The API's own tables (SPEC §6.3). Enums are VARCHAR + CHECK, as in pingit (easier to migrate than ENUM)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, TimestampMixin, UtcDateTime


def _check_in(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in enum)})"


class DashRole(StrEnum):
    TRAINER = "trainer"
    ADMIN = "admin"


class DashUser(TimestampMixin, Base):
    __tablename__ = "dash_users"
    __table_args__ = (CheckConstraint(_check_in("role", DashRole), name="ck_dash_users_role"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)  # stored lowercased
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # argon2id
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=DashRole.TRAINER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Chicago")
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # theme, saved filters, table columns


class DashRefreshToken(Base):
    __tablename__ = "dash_refresh_tokens"
    __table_args__ = (Index("ix_dash_refresh_tokens_family", "family_id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # SHA-256 hex
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    replaced_by_id: Mapped[int | None] = mapped_column(BigIntId)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class DashPasswordResetToken(Base):
    __tablename__ = "dash_password_reset_tokens"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class DashAuditLog(Base):
    """Append-only: who played which recording, viewed which transcript, exported what, published what."""

    __tablename__ = "dash_audit_log"
    __table_args__ = (
        Index("ix_dash_audit_log_actor", "actor_user_id", "created_at"),
        Index("ix_dash_audit_log_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"


class DashEmailOutbox(Base):
    """Password-reset emails, sent by the worker through Microsoft Graph (never inside a request)."""

    __tablename__ = "dash_email_outbox"
    __table_args__ = (
        CheckConstraint(_check_in("status", OutboxStatus), name="ck_dash_email_outbox_status"),
        Index("ix_dash_email_outbox_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=OutboxStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class QueueStatus(StrEnum):
    OPEN = "open"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


class QueueResolution(StrEnum):
    SCRIPT_CHANGED = "script_changed"
    AGENT_ISSUE = "agent_issue"
    NO_ACTION = "no_action"
    OTHER = "other"


class ReviewQueueItem(Base):
    """The quality queue's state for a flagged session. The session_id matches session_reviews.session_id (an
    agent table, so no database foreign key: the agent's tables stay independent of the API's)."""

    __tablename__ = "review_queue"
    __table_args__ = (
        CheckConstraint(_check_in("status", QueueStatus), name="ck_review_queue_status"),
        CheckConstraint(
            f"resolution IS NULL OR {_check_in('resolution', QueueResolution)}",
            name="ck_review_queue_resolution",
        ),
        Index("ix_review_queue_status", "status", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=QueueStatus.OPEN.value)
    assignee_user_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(String(32))
    updated_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), onupdate=func.utc_timestamp(6), nullable=False
    )


class SavedView(Base):
    __tablename__ = "saved_views"
    __table_args__ = (Index("ix_saved_views_user_page", "user_id", "page"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False)
    page: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class JobType(StrEnum):
    EXPORT = "export"
    PREPARE_FOR_VOICE = "prepare_for_voice"  # Stage 2
    CREATE_VOCABULARY = "create_vocabulary"  # Stage 2


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    """Background work run by the worker (SPEC §6.5): XLSX exports now, training preparation in Stage 2."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_check_in("type", JobType), name="ck_jobs_type"),
        CheckConstraint(_check_in("status", JobStatus), name="ck_jobs_status"),
        Index("ix_jobs_status", "status", "created_at"),
        Index("ix_jobs_creator", "created_by", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    training_id: Mapped[str | None] = mapped_column(String(50))
    version_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
