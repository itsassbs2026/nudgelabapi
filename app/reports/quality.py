"""Quality queue (SPEC §7.2): sessions the daily AI review flagged, and what the team did about each one.

Flags come from the agent's `session_reviews`; the team's work (status, resolution, note, assignee) lives in
the API's `review_queue`, created the first time someone acts on a flagged session. No row means "open".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session, aliased

from app.models.dashboard import DashUser, QueueStatus, ReviewQueueItem
from app.reference.agent_tables import session_reviews, training_sessions, trainings, vw_trainees
from app.reports import metrics
from app.reports.filters import ReportFilters, session_conditions
from app.services import audit
from app.utils.errors import ApiError

STATUSES = tuple(s.value for s in QueueStatus)
queue = ReviewQueueItem.__table__


def issue_types(issues: Any) -> list[str]:
    if not isinstance(issues, list):
        return []
    seen: list[str] = []
    for issue in issues:
        kind = issue.get("type") if isinstance(issue, dict) else None
        if kind and kind not in seen:
            seen.append(str(kind))
    return seen


def quality_list(
    db: Session,
    f: ReportFilters,
    *,
    status: str | None = None,
    issue_type: str | None = None,
    assignee_user_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    s = training_sessions.c
    rv = session_reviews.c
    q = queue.c
    assignee = aliased(DashUser)
    editor = aliased(DashUser)
    status_col = func.coalesce(q.status, QueueStatus.OPEN.value)

    conditions: list[ColumnElement[bool]] = [rv.flagged == 1, *session_conditions(f)]
    if issue_type:
        conditions.append(func.json_search(rv.issues, "one", issue_type, None, "$[*].type").is_not(None))
    if assignee_user_id is not None:
        conditions.append(q.assignee_user_id == assignee_user_id)

    base = (
        select(
            s.session_id,
            s.started_at,
            s.uid,
            vw_trainees.c.name,
            s.training_id,
            trainings.c.title,
            rv.score,
            rv.summary,
            rv.issues,
            rv.issue_count,
            status_col.label("status"),
            q.resolution,
            q.note,
            q.assignee_user_id,
            assignee.full_name.label("assignee_name"),
            q.updated_at,
            editor.full_name.label("updated_by_name"),
        )
        .select_from(
            metrics.sessions_from()
            .join(session_reviews, rv.session_id == s.session_id)
            .outerjoin(vw_trainees, vw_trainees.c.uid == s.uid)
            .outerjoin(queue, q.session_id == s.session_id)
            .outerjoin(assignee, assignee.id == q.assignee_user_id)
            .outerjoin(editor, editor.id == q.updated_by)
        )
        .where(and_(*conditions))
    ).subquery()

    counts = dict.fromkeys(STATUSES, 0)
    for value, n in db.execute(select(base.c.status, func.count()).group_by(base.c.status)):
        counts[str(value)] = int(n)
    listing = select(base)
    if status:
        listing = listing.where(base.c.status == status)
    total = int(db.execute(select(func.count()).select_from(listing.subquery())).scalar_one())
    rows = db.execute(
        listing.order_by(base.c.started_at.desc(), base.c.session_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "counts": counts,
        "items": [
            {
                "session_id": r.session_id,
                "started_at": r.started_at,
                "uid": r.uid,
                "name": r.name,
                "training_id": r.training_id,
                "training_title": r.title,
                "score": r.score,
                "summary": r.summary,
                "issue_types": issue_types(r.issues),
                "issue_count": int(r.issue_count or 0),
                "status": r.status,
                "resolution": r.resolution,
                "note": r.note,
                "assignee_user_id": r.assignee_user_id,
                "assignee_name": r.assignee_name,
                "updated_at": r.updated_at,
                "updated_by_name": r.updated_by_name,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def queue_state(db: Session, session_id: str) -> dict[str, Any] | None:
    """The queue fields for one flagged session (None if the session isn't flagged)."""
    flagged = db.execute(
        select(session_reviews.c.flagged).where(session_reviews.c.session_id == session_id)
    ).scalar_one_or_none()
    if not flagged:
        return None
    item = db.get(ReviewQueueItem, session_id)
    return {
        "status": item.status if item else QueueStatus.OPEN.value,
        "resolution": item.resolution if item else None,
        "note": item.note if item else None,
        "assignee_user_id": item.assignee_user_id if item else None,
        "updated_at": item.updated_at if item else None,
    }


def update_item(
    db: Session, actor: DashUser, session_id: str, changes: dict[str, Any], *, ip: str | None
) -> dict[str, Any]:
    """Set status, resolution, note and/or assignee on a flagged session. Only the given fields change."""
    if queue_state(db, session_id) is None:
        raise ApiError(404, "not_in_queue", "This session isn't flagged for review.")
    if changes.get("assignee_user_id") is not None:
        person = db.get(DashUser, changes["assignee_user_id"])
        if person is None or not person.is_active:
            raise ApiError(422, "invalid_assignee", "Choose an active dashboard user.")
    item = db.get(ReviewQueueItem, session_id)
    if item is None:
        item = ReviewQueueItem(session_id=session_id, status=QueueStatus.OPEN.value)
        db.add(item)
    before = {k: getattr(item, k) for k in changes}
    for key, value in changes.items():
        setattr(item, key, value)
    item.updated_by = actor.id
    audit.record(
        db,
        audit.AuditAction.QUALITY_UPDATED,
        actor_user_id=actor.id,
        target_type="session",
        target_id=session_id,
        details={"before": before, "after": changes},
        ip=ip,
    )
    db.commit()
    state = queue_state(db, session_id)
    assert state is not None
    return state
