"""Reset a person's training progress (2026-10-22): one training or all of them.

The person starts the training brand new on their next call: nothing covered, no quiz answers, no pinned
version (they get the live one), no question-pool draw. Nothing is lost: each progress row is copied to
`training_progress_history` first, sessions and recordings are untouched, and every earlier pass stays in
`training_pass_log`. An assignment that was marked completed goes back to assigned (its due date is not
changed: the due-date tool does that).

If they had passed, their Prime Portal completion has to go too. Only the agent servers hold a Portal write
login, so the reset queues a row in `portal_clears` and the agent deletes the Portal record within a minute
(agent repo completions.py `clear`). If they pass again before that runs, the new pass is kept.

A reset is refused while the person is on a call about the training (a call in progress writes its own
progress and would put it back).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

IN_CALL_MINUTES = 30  # a session with no end this recently is a call in progress

_ARCHIVE = text(
    """INSERT INTO training_progress_history
           (uid, training_id, track, version_id, status, topics_covered, walkthrough_finished_at,
            quiz_attempted, correct_questions, sessions_count, first_started_at, passed_at, last_session_id,
            archived_reason)
       SELECT uid, training_id, track, version_id, status, topics_covered, walkthrough_finished_at,
              quiz_attempted, correct_questions, sessions_count, first_started_at, passed_at, last_session_id,
              :reason
       FROM training_progress WHERE uid = :uid AND training_id = :tid"""
)
_RESET = text(
    """UPDATE training_progress
       SET status = 'not_started', topics_covered = JSON_ARRAY(), walkthrough_finished_at = NULL,
           quiz_attempted = 0, correct_questions = JSON_ARRAY(), quiz_draw = NULL, first_started_at = NULL,
           passed_at = NULL, version_id = NULL
       WHERE uid = :uid AND training_id = :tid"""
)
_REOPEN = text(
    "UPDATE training_assignments SET status = 'assigned'"
    " WHERE uid = :uid AND training_id = :tid AND status = 'completed'"
)
_QUEUE_CLEAR = text(
    "INSERT INTO portal_clears (uid, training_id, completion_key, requested_by)"
    " VALUES (:uid, :tid, :key, :by)"
)


def _started(row: Any) -> bool:
    """There is something to reset: they started, passed, or have progress recorded."""
    return bool(row.status != "not_started" or row.first_started_at or row.passed_at or row.quiz_attempted)


def reset(
    db: Session, current: CurrentUser, uid: int, training_id: str | None, reason: str | None, ip: str | None
) -> dict[str, Any]:
    """Resets `training_id` for `uid`, or every training they have progress on when it is None."""
    params: dict[str, Any] = {"uid": uid}
    only = ""
    if training_id:
        params["tid"] = training_id
        only = " AND p.training_id = :tid"
    rows = (
        db.execute(
            text(
                "SELECT p.training_id, t.title, t.completion_key, p.status, p.first_started_at, p.passed_at,"
                " p.quiz_attempted FROM training_progress p"
                " LEFT JOIN trainings t ON t.training_id = p.training_id"
                f" WHERE p.uid = :uid{only} ORDER BY p.training_id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    todo = [r for r in rows if _started(r)]
    if training_id and not todo:
        raise ApiError(
            404, "nothing_to_reset", "They haven't started this training, so there is nothing to reset."
        )
    if not todo:
        return {"reset": [], "skipped": len(rows)}

    busy = db.execute(
        text(
            "SELECT training_id FROM training_sessions WHERE uid = :uid AND ended_at IS NULL"
            f" AND started_at > UTC_TIMESTAMP() - INTERVAL {IN_CALL_MINUTES} MINUTE"
            + (" AND training_id = :tid" if training_id else "")
        ),
        params,
    ).first()
    if busy:
        raise ApiError(
            409,
            "call_in_progress",
            "They are on a call right now, and a reset would be overwritten by it. Try again when it ends.",
        )

    label = f"reset by {current.user.full_name}"
    archived_reason = (f"{label}: {reason}" if reason else label)[:100]
    done: list[dict[str, Any]] = []
    for r in todo:
        keys = {"uid": uid, "tid": r["training_id"]}
        db.execute(_ARCHIVE, {**keys, "reason": archived_reason})
        db.execute(_RESET, keys)
        db.execute(_REOPEN, keys)
        queued = bool(r["completion_key"])
        if queued:
            db.execute(_QUEUE_CLEAR, {**keys, "key": r["completion_key"], "by": current.user.id})
        done.append(
            {
                "training_id": r["training_id"],
                "title": r["title"] or r["training_id"],
                "was_passed": r["passed_at"] is not None,
                "portal": "queued" if queued else "not_linked",
            }
        )
    audit.record(
        db,
        AuditAction.PROGRESS_RESET,
        actor_user_id=current.user.id,
        target_type="employee",
        target_id=uid,
        details={
            "trainings": [d["training_id"] for d in done],
            "passed_before": [d["training_id"] for d in done if d["was_passed"]],
            "portal_clears": sum(1 for d in done if d["portal"] == "queued"),
            "reason": reason,
        },
        ip=ip,
    )
    db.commit()
    return {"reset": done, "skipped": len(rows) - len(todo)}
