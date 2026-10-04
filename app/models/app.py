"""Tables for the Flutter app's endpoints (docs/APP_HANDOFF.md)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class AppSessionStart(Base):
    """One row per LiveKit token handed to the app. The session itself is the agent's `training_sessions` row
    (same room name), written only if the trainee actually joins."""

    __tablename__ = "app_session_starts"
    __table_args__ = (
        Index("ix_app_session_starts_uid", "uid", "created_at"),
        Index("ix_app_session_starts_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    training_id: Mapped[str] = mapped_column(String(50), nullable=False)
    room_name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_over: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pass_jti: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(45))
    trainer_name: Mapped[str | None] = mapped_column(String(40))  # the name the trainer said (persona)
    voice_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
