"""Earlier saves of a draft (2026-10-22): snapshots, the list, and putting one back.

The Studio autosaves, so a draft needs a way back from a mistake. Before a save overwrites the draft's
content, that content is kept as a snapshot, at most one per `studio.history_min_minutes` (10) and the
newest `studio.history_keep` (20) per draft; both are Admin settings, and 0 for `history_keep` turns
history off. Restoring a snapshot makes it the content again, after snapshotting what was there, so a
restore can be undone.

History is a safety net, never a reason for a save to fail: taking a snapshot runs in a savepoint and
any error is logged and dropped (for example before the table's grants are in place).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.models.dashboard import DashUser
from app.models.studio_history import DashVersionHistory as H
from app.reference.agent_tables import training_versions
from app.services import audit, settings_table
from app.services.audit import AuditAction
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


def settings(db: Session) -> dict[str, int]:
    """What the dashboard needs to know: how soon to autosave, and how history is kept."""
    return {
        "autosave_seconds": int(settings_table.get(db, "studio.autosave_seconds", 20)),
        "history_keep": int(settings_table.get(db, "studio.history_keep", 20)),
        "history_min_minutes": int(settings_table.get(db, "studio.history_min_minutes", 10)),
        "stale_draft_days": int(settings_table.get(db, "studio.stale_draft_days", 60)),
    }


def summarize(content: Any) -> dict[str, int]:
    """Enough to tell two saves apart in a list: topics, quiz questions and characters."""
    doc = _json(content) or {}
    topics = ((doc.get("knowledge_base") or {}).get("topics")) or []
    questions = ((doc.get("quiz") or {}).get("questions")) or []
    return {
        "topics": len(topics),
        "questions": len(questions),
        "characters": len(json.dumps(doc, ensure_ascii=False)),
    }


def _prune(db: Session, version_id: int, keep: int) -> None:
    old = db.scalars(select(H.id).where(H.version_id == version_id).order_by(H.id.desc()).offset(keep)).all()
    if old:
        db.execute(delete(H).where(H.id.in_(old)))


def snapshot(db: Session, row: Any, *, force: bool = False) -> bool:
    """Keep `row`'s content as it stands (before a save changes it). Not more often than the settings allow,
    unless `force`. Returns whether one was taken. Never raises: history must not break saving."""
    try:
        with db.begin_nested():
            cfg = settings(db)
            if cfg["history_keep"] <= 0 or row.content is None:
                return False
            if not force:
                last = db.scalar(select(func.max(H.created_at)).where(H.version_id == row.version_id))
                if last and _now() - last < timedelta(minutes=cfg["history_min_minutes"]):
                    return False
            content = _json(row.content)
            db.add(
                H(
                    version_id=row.version_id,
                    revision=row.revision,
                    content=content,
                    summary=summarize(content),
                    saved_at=row.updated_at or row.created_at,
                    saved_by=row.updated_by or row.created_by,
                )
            )
            db.flush()
            _prune(db, row.version_id, cfg["history_keep"])
        return True
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        logger.warning("version_history_snapshot_failed", version_id=row.version_id, error=type(exc).__name__)
        return False


def listing(db: Session, version_id: int) -> list[dict[str, Any]]:
    """The draft's earlier saves, newest first (no content: that's for restoring)."""
    rows = db.execute(
        select(H.id, H.revision, H.summary, H.saved_at, H.saved_by, H.created_at)
        .where(H.version_id == version_id)
        .order_by(H.id.desc())
    ).all()
    names = {
        r.id: r.full_name
        for r in db.execute(
            select(DashUser.id, DashUser.full_name).where(
                DashUser.id.in_({x.saved_by for x in rows if x.saved_by})
            )
        )
    }
    return [
        {
            "id": r.id,
            "revision": r.revision,
            "saved_at": r.saved_at or r.created_at,
            "saved_by": names.get(r.saved_by),
            "summary": _json(r.summary),
        }
        for r in rows
    ]


def restore(
    db: Session, current: CurrentUser, version_id: int, history_id: int, revision: int, ip: str | None
) -> dict[str, Any]:
    """Put an earlier save back as the draft's content. `revision` is the revision the person is looking at:
    if someone saved since, it's an edit conflict like any save. What was there is kept in history first."""
    from app.studio import service  # circular at import time

    row = service._version(db, version_id)
    if row.status != service.DRAFT:
        raise ApiError(409, "not_a_draft", "Only a draft can be restored to an earlier save.")
    saved = db.get(H, history_id)
    if saved is None or saved.version_id != version_id:
        raise ApiError(
            404, "not_found", "That earlier save isn't there (it may have been replaced by newer ones)."
        )
    if row.revision != revision:
        raise service.edit_conflict(db, version_id)
    snapshot(db, row, force=True)
    v = training_versions.c
    result = db.execute(
        update(training_versions)
        .where(v.version_id == version_id, v.revision == revision, v.status == service.DRAFT)
        .values(content=saved.content, revision=v.revision + 1, updated_at=_now(), updated_by=current.user.id)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise service.edit_conflict(db, version_id)
    audit.record(
        db,
        AuditAction.VERSION_HISTORY_RESTORED,
        actor_user_id=current.user.id,
        target_type="training",
        target_id=row.training_id,
        details={"version_id": version_id, "history_id": history_id, "saved_at": str(saved.saved_at)},
        ip=ip,
    )
    db.commit()
    return service.version_summary(db, version_id)
