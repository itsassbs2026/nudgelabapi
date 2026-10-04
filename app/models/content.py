"""Uploaded source documents for the training studio (SPEC 6.5 `content_uploads`, Phase 12)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime


class UploadStatus(StrEnum):
    PENDING = "pending"  # upload link issued, file not confirmed yet
    PROCESSING = "processing"  # file confirmed; the worker is checking it and extracting the text
    READY = "ready"  # text extracted
    REJECTED = "rejected"  # not the type it claimed, unreadable, too large, or no text (see `error`)


class ContentUpload(Base):
    __tablename__ = "content_uploads"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(repr(s.value) for s in UploadStatus) + ")",
            name="ck_content_uploads_status",
        ),
        Index("ix_content_uploads_training", "training_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    training_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # Random, never derived from the file name (SPEC §12.4). Pending files live under <prefix>/pending/,
    # checked ones under <prefix>/uploads/.
    s3_key: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)  # shown to people only
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UploadStatus.PENDING.value)
    extracted_text: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql", "mariadb"))
    error: Mapped[str | None] = mapped_column(String(300))
    uploaded_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
