"""Testers of the agent's tester page (SPEC 6.5 `testers`, Phase 14), replacing its testers.json.

The agent's web.py reads this table to sign testers in. Access codes are never stored: `code_hash` is the
SHA-256 of the code, trimmed and uppercased, exactly as web.py hashes what a tester types, so codes made here,
codes made by `web.py add-tester`, and codes imported from testers.json all work the same way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, TimestampMixin, UtcDateTime


class Tester(TimestampMixin, Base):
    __tablename__ = "testers"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    # Their sessions are recorded under this uid.
    uid: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trainings: Mapped[list[Any]] = mapped_column(JSON, nullable=False)  # training ids they may start
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    code_changed_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6)
    )
