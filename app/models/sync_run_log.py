"""One row per table per run of a sync (2026-10-06: the PortalLive reference-table sync,
app/reference/sync.py).

Written by the sync's own login (`nudgelab_sync`), success or failure, each in its own commit. Times are UTC.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class SyncRunLog(Base):
    __tablename__ = "sync_run_log"
    __table_args__ = (Index("ix_sync_run_log_name_started", "sync_name", "started_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    sync_name: Mapped[str] = mapped_column(String(64), nullable=False)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success or failed
    row_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
