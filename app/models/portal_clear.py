"""A request to clear a person's completion record in Prime Portal (2026-10-22).

Resetting someone's progress (app/services/progress_reset.py) must also take the class off Portal's
"completed" list, but only the agent servers have a Portal write login. So the reset queues a row here and
the agent clears the Portal row within a minute (agent repo completions.py `clear`), then marks it done.
`status` is pending, done, skipped (they passed again before it ran, so the new pass is kept) or failed (gave
up after repeated errors).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class PortalClear(Base):
    __tablename__ = "portal_clears"
    __table_args__ = (Index("ix_portal_clears_status", "status", "id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    training_id: Mapped[str] = mapped_column(String(50), nullable=False)
    completion_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    requested_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    error: Mapped[str | None] = mapped_column(String(500))
