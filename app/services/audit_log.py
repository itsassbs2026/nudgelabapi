"""Reading the audit log (Admin)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.dashboard import DashAuditLog, DashUser


@dataclass(frozen=True)
class AuditRow:
    entry: DashAuditLog
    actor_email: str | None


@dataclass(frozen=True)
class AuditPage:
    items: list[AuditRow]
    total: int


def list_entries(
    db: Session,
    *,
    action: str | None,
    actor_user_id: int | None,
    target_type: str | None,
    target_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    page_size: int,
) -> AuditPage:
    query = select(DashAuditLog, DashUser.email).outerjoin(
        DashUser, DashUser.id == DashAuditLog.actor_user_id
    )
    if action:
        query = query.where(DashAuditLog.action == action)
    if actor_user_id is not None:
        query = query.where(DashAuditLog.actor_user_id == actor_user_id)
    if target_type:
        query = query.where(DashAuditLog.target_type == target_type)
    if target_id:
        query = query.where(DashAuditLog.target_id == target_id)
    if date_from:
        query = query.where(DashAuditLog.created_at >= date_from)
    if date_to:
        query = query.where(DashAuditLog.created_at < date_to)
    total = int(db.execute(select(func.count()).select_from(query.subquery())).scalar_one())
    rows = db.execute(
        query.order_by(DashAuditLog.created_at.desc(), DashAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return AuditPage(items=[AuditRow(entry=r[0], actor_email=r[1]) for r in rows], total=total)
