"""Which dashboard actions each role may take (2026-10-07), switched by Admins on Admin > Permissions.

One row per action and role. Admins may always take every action, so only the Trainer rows mean anything
today; the table is keyed by role so a new role later is new rows, not new code (app/services/permissions.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class DashPermission(Base):
    __tablename__ = "dash_permissions"

    action: Mapped[str] = mapped_column(String(40), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), onupdate=func.utc_timestamp(6), nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
