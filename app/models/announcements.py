"""What's new (2026-10-15): the announcements each dashboard user has seen, so a tour shows once per person.

The announcements themselves (titles, steps) ship with the dashboard; only the key is stored here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class DashAnnouncementSeen(Base):
    __tablename__ = "dash_announcements_seen"

    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), primary_key=True)
    announcement: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
