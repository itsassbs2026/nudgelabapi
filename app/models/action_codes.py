"""One-time email codes that confirm a sensitive Admin action (2026-10-06: "Sync user/store list from
Portal").

The code itself is never stored: `code_hash` is an HMAC of it with the server's secret, so a copy of the table
can't be used to guess codes offline. A code works once, for one user and one purpose, until it expires or
after too many wrong tries (app/reference/manual.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class DashActionCode(Base):
    __tablename__ = "dash_action_codes"
    __table_args__ = (Index("ix_dash_action_codes_user", "user_id", "purpose", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
