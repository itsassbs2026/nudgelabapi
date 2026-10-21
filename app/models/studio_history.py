"""Earlier saves of a draft (2026-10-22): the Studio autosaves, so each draft keeps a short history to go
back to.

A snapshot is the draft's content as it stood before a save, taken at most once per
`studio.history_min_minutes` and trimmed to the newest `studio.history_keep` per draft (Admin >
Settings). Restoring one makes it the draft's content again (and snapshots what was there, so a restore
can be undone too). No foreign key to the agent's `training_versions`: the agent's tables stay
independent of the API's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class DashVersionHistory(Base):
    __tablename__ = "dash_version_history"
    __table_args__ = (Index("ix_dash_version_history_version", "version_id", "id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int | None] = mapped_column(Integer)  # the draft's revision this content was saved as
    content: Mapped[Any] = mapped_column(JSON, nullable=False)
    summary: Mapped[Any] = mapped_column(
        JSON, nullable=False
    )  # topics, questions, characters: to tell saves apart
    saved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # when this content was saved
    saved_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )  # when the snapshot was taken
