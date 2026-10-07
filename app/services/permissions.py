"""Which dashboard actions each role may take (dash_permissions, 2026-10-07).

Admins may always take every action, so they can never lock themselves out. For other roles a missing row
means "not allowed". The route checks the permission itself; the dashboard only hides what isn't allowed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.models.dashboard import DashRole
from app.models.permissions import DashPermission
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError


class Action(StrEnum):
    ASSIGN_SINGLE = "assign_single"
    ASSIGN_BULK = "assign_bulk"
    ASSIGN_CANCEL = "assign_cancel"
    ASSIGN_DUE_DATE = "assign_due_date"


LABELS = {
    Action.ASSIGN_SINGLE: "Assign training to one person",
    Action.ASSIGN_BULK: "Mass upload assignments (CSV)",
    Action.ASSIGN_CANCEL: "Cancel assignments",
    Action.ASSIGN_DUE_DATE: "Change due dates",
}
# Roles whose switches Admins set. Admins themselves are always allowed.
SWITCHABLE_ROLES = (DashRole.TRAINER.value,)


def allowed(db: Session, current: CurrentUser, action: Action) -> bool:
    if current.is_admin:
        return True
    row = db.get(DashPermission, (action.value, current.role))
    return bool(row and row.enabled)


def mine(db: Session, current: CurrentUser) -> dict[str, bool]:
    if current.is_admin:
        return {a.value: True for a in Action}
    rows = db.scalars(select(DashPermission).where(DashPermission.role == current.role)).all()
    on = {r.action for r in rows if r.enabled}
    return {a.value: a.value in on for a in Action}


def require(action: Action) -> Any:
    """A route dependency: the signed-in user may take `action`."""

    def check(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> CurrentUser:
        if not allowed(db, current, action):
            raise ApiError(403, "forbidden", "Your role isn't allowed to do this. An Admin can turn it on.")
        return current

    return check


def table(db: Session) -> list[dict[str, Any]]:
    rows = {(r.action, r.role): r.enabled for r in db.scalars(select(DashPermission)).all()}
    return [
        {
            "action": a.value,
            "label": LABELS[a],
            "roles": {role: rows.get((a.value, role), False) for role in SWITCHABLE_ROLES},
        }
        for a in Action
    ]


def update(
    db: Session, current: CurrentUser, changes: list[dict[str, Any]], ip: str | None
) -> list[dict[str, Any]]:
    """Sets each {action, role, enabled}; records what actually changed."""
    changed = []
    for change in changes:
        action, role, enabled = Action(change["action"]), change["role"], bool(change["enabled"])
        if role not in SWITCHABLE_ROLES:
            raise ApiError(422, "unknown_role", f"Permissions can't be set for the role {role!r}.")
        row = db.get(DashPermission, (action.value, role))
        if row is None:
            row = DashPermission(action=action.value, role=role, enabled=enabled, updated_by=current.user.id)
            db.add(row)
        elif row.enabled == enabled:
            continue
        else:
            row.enabled = enabled
            row.updated_by = current.user.id
        changed.append({"action": action.value, "role": role, "enabled": enabled})
    if changed:
        audit.record(db, AuditAction.PERMISSIONS_CHANGED, actor_user_id=current.user.id,
                     target_type="permissions", details={"changes": changed}, ip=ip)  # fmt: skip
    db.commit()
    return table(db)
