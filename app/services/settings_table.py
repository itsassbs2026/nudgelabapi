"""`dash_settings`: Admin-managed tunables (Admin > Settings, docs/ASK_NUDGELAB.md). Values are stored as text
with their type; `get` returns them typed, cached for a short while so a busy page doesn't read the table on
every call.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.models.ask import DashSetting
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

CACHE_SECONDS = 30
_cache: dict[str, tuple[float, Any]] = {}


def _typed(row: DashSetting) -> Any:
    if row.type == "int":
        return int(row.value)
    if row.type == "bool":
        return row.value.strip().lower() in ("1", "true", "yes", "on")
    if row.type == "decimal":
        return Decimal(row.value)
    return row.value


def get(db: Session, key: str, default: Any = None) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    row = db.get(DashSetting, key)
    value = _typed(row) if row is not None else default
    _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _cache.clear()


def list_settings(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(DashSetting).order_by(DashSetting.key)).scalars()
    return [
        {
            "key": r.key,
            "value": r.value,
            "type": r.type,
            "description": r.description,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


def _check(row: DashSetting, value: str) -> str:
    value = value.strip()
    try:
        if row.type == "int":
            if int(value) < 0:
                raise ValueError
            return str(int(value))
        if row.type == "bool":
            if value.lower() not in ("true", "false"):
                raise ValueError
            return value.lower()
        if row.type == "decimal":
            if Decimal(value) < 0:
                raise ValueError
            return str(Decimal(value))
    except (ValueError, InvalidOperation):
        raise ApiError(
            422,
            "bad_value",
            {
                "int": "Enter a whole number, 0 or more.",
                "bool": "Enter true or false.",
                "decimal": "Enter a number, 0 or more.",
            }[row.type],
        ) from None
    if not value or len(value) > 500:
        raise ApiError(422, "bad_value", "Enter a value (at most 500 characters).")
    return value


def set_value(db: Session, current: CurrentUser, key: str, value: str, ip: str | None) -> dict[str, Any]:
    row = db.get(DashSetting, key)
    if row is None:
        raise ApiError(404, "not_found", "There's no setting with that name.")
    before, row.value, row.updated_by = row.value, _check(row, value), current.user.id
    audit.record(
        db,
        AuditAction.SETTINGS_CHANGED,
        actor_user_id=current.user.id,
        target_type="setting",
        target_id=key,
        details={"from": before, "to": row.value},
        ip=ip,
    )
    db.commit()
    _cache.pop(key, None)
    return {
        "key": row.key,
        "value": row.value,
        "type": row.type,
        "description": row.description,
        "updated_at": row.updated_at,
    }
