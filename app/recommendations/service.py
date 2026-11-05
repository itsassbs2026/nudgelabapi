"""Recommendations in the Studio: a training's list, open counts for the Studio list, and done / dismissed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.models.dashboard import DashUser
from app.models.recommendations import DISMISSED, DONE, OPEN, DashRecommendation, DashRecommendationRun
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

STATUSES = (OPEN, DONE, DISMISSED)
SHOWN = (OPEN, DONE, DISMISSED)  # replaced ones are history
IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}


def open_counts(db: Session) -> dict[str, int]:
    r = DashRecommendation
    return {
        tid: int(n)
        for tid, n in db.execute(
            select(r.training_id, func.count()).where(r.status == OPEN).group_by(r.training_id)
        )
    }


def _out(r: DashRecommendation, by: str | None) -> dict[str, Any]:
    return {
        "id": r.id,
        "training_id": r.training_id,
        "version_id": r.version_id,
        "area": r.area,
        "target": r.target,
        "impact": r.impact,
        "title": r.title,
        "problem": r.problem,
        "suggestion": r.suggestion,
        "evidence": r.evidence,
        "examples": r.examples or [],
        "status": r.status,
        "status_note": r.status_note,
        "status_by": by,
        "status_at": r.status_at,
        "created_at": r.created_at,
    }


def for_training(db: Session, training_id: str) -> dict[str, Any]:
    """The open recommendations, newest run first by impact, and the last 20 done or dismissed."""
    r = DashRecommendation
    rows = db.execute(
        select(r, DashUser.full_name)
        .outerjoin(DashUser, DashUser.id == r.status_by)
        .where(r.training_id == training_id, r.status.in_(SHOWN))
        .order_by(r.created_at.desc())
    ).all()
    current = [_out(x, by) for x, by in rows if x.status == OPEN]
    current.sort(key=lambda x: IMPACT_ORDER.get(x["impact"], 1))
    handled = [_out(x, by) for x, by in rows if x.status != OPEN][:20]
    last = db.execute(
        select(DashRecommendationRun)
        .where(DashRecommendationRun.training_id == training_id, DashRecommendationRun.error.is_(None))
        .order_by(DashRecommendationRun.run_date.desc(), DashRecommendationRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "training_id": training_id,
        "open": current,
        "handled": handled,
        "last_run": (
            {
                "run_date": last.run_date,
                "window_from": last.window_from,
                "window_to": last.window_to,
                "calls": last.calls,
            }
            if last
            else None
        ),
    }


def set_status(
    db: Session, current: CurrentUser, rec_id: int, status: str, note: str | None, ip: str | None
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ApiError(422, "bad_status", "Status must be open, done or dismissed.")
    row = db.get(DashRecommendation, rec_id)
    if row is None or row.status not in SHOWN:
        raise ApiError(404, "not_found", "That recommendation isn't current any more.")
    row.status = status
    row.status_note = " ".join((note or "").split())[:300] or None
    row.status_by = current.user.id if status != OPEN else None
    row.status_at = datetime.now(UTC).replace(tzinfo=None) if status != OPEN else None
    audit.record(db, AuditAction.RECOMMENDATION_UPDATED, actor_user_id=current.user.id,
                 target_type="recommendation", target_id=str(rec_id),
                 details={"training_id": row.training_id, "status": status, "note": row.status_note},
                 ip=ip)  # fmt: skip
    db.commit()
    return _out(row, current.user.full_name if status != OPEN else None)
