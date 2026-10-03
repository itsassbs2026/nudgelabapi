"""Importing this package registers every API model on Base.metadata (used by Alembic)."""

from app.models.base import Base
from app.models.dashboard import (
    DashAuditLog,
    DashEmailOutbox,
    DashPasswordResetToken,
    DashRefreshToken,
    DashUser,
    Job,
    ReviewQueueItem,
    SavedView,
)

API_TABLES = frozenset(Base.metadata.tables)

__all__ = [
    "API_TABLES",
    "Base",
    "DashAuditLog",
    "DashEmailOutbox",
    "DashPasswordResetToken",
    "DashRefreshToken",
    "DashUser",
    "Job",
    "ReviewQueueItem",
    "SavedView",
]
