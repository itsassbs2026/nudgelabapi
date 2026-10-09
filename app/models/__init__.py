"""Importing this package registers every API model on Base.metadata (used by Alembic)."""

from app.models.action_codes import DashActionCode
from app.models.agent_servers import AgentServer
from app.models.app import AppSessionStart
from app.models.base import Base
from app.models.content import ContentUpload
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
from app.models.permissions import DashPermission
from app.models.summary import DashSummaryRecipient, DashSummaryRun
from app.models.sync_run_log import SyncRunLog
from app.models.testers import Tester

API_TABLES = frozenset(Base.metadata.tables)

__all__ = [
    "API_TABLES",
    "AgentServer",
    "AppSessionStart",
    "Base",
    "ContentUpload",
    "DashActionCode",
    "DashAuditLog",
    "DashEmailOutbox",
    "DashPasswordResetToken",
    "DashPermission",
    "DashRefreshToken",
    "DashSummaryRecipient",
    "DashSummaryRun",
    "DashUser",
    "Job",
    "ReviewQueueItem",
    "SavedView",
    "SyncRunLog",
    "Tester",
]
