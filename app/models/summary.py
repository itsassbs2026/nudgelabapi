"""The daily summary email (2026-10-09): who gets it, and a record of each day's send.

Recipients are managed by Admins on Admin > Daily summary. `dash_summary_runs` has one row per report day, so
the 7 AM timer (and a rerun after a reboot) never sends the same day twice; a test send isn't recorded there.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class DashSummaryRecipient(Base):
    __tablename__ = "dash_summary_recipients"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class DashSummaryRun(Base):
    __tablename__ = "dash_summary_runs"

    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    recipients: Mapped[int] = mapped_column(Integer, nullable=False)
    by_claude: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
