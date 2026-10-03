"""The app's training list and badge (docs/APP_HANDOFF.md §3.1–3.2).

The JSON keeps the shape of Wanaka's `usp_ai_trainer_assignments_json` (and its pending count), so the app's
screens keep working, with new keys added. Who has which training comes from `training_assignments` only;
progress and completion come from the agent's own `training_progress`.

A training is listed when it's assigned (not cancelled), shown in the app (`app_status = 'active'`), not
retired, and has a published version. The badge counts the listed trainings that are required and not
completed, from the same rows, so the list and the badge always agree.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.reference.agent_tables import training_assignments, training_progress, training_topics, trainings

LISTED_ASSIGNMENT_STATUSES = ("assigned", "completed")
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"  # as the Wanaka procedure formats assigned_at


def _wanaka_time(value: datetime | None) -> str | None:
    return value.strftime(_TIME_FORMAT) if value else None


def _listed(uid: int) -> Select[Any]:
    a, t, p = training_assignments, trainings, training_progress
    return (
        select(
            a.c.training_id,
            a.c.assigned_at,
            a.c.due_at,
            a.c.matched_rule_group_id,
            a.c.matched_rule_name,
            a.c.ai_flag,
            t.c.title,
            t.c.app_title,
            t.c.category,
            t.c.tags,
            t.c.description,
            t.c.is_required,
            t.c.wanaka_trainer_id,
            t.c.completion_key,
            t.c.completion_type,
            t.c.active_version_id,
            p.c.version_id.label("progress_version_id"),
            p.c.topics_covered,
            p.c.quiz_attempted,
            p.c.sessions_count,
            p.c.first_started_at,
            p.c.passed_at,
        )
        .join(t, t.c.training_id == a.c.training_id)
        .outerjoin(p, and_(p.c.uid == a.c.uid, p.c.training_id == a.c.training_id))
        .where(
            a.c.uid == uid,
            a.c.status.in_(LISTED_ASSIGNMENT_STATUSES),
            t.c.app_status == "active",
            t.c.status != "retired",
            t.c.active_version_id.is_not(None),
        )
    )


def _topic_counts(db: Session, version_ids: set[int]) -> dict[int, int]:
    if not version_ids:
        return {}
    rows = db.execute(
        select(training_topics.c.version_id, func.count())
        .where(training_topics.c.version_id.in_(version_ids))
        .group_by(training_topics.c.version_id)
    ).all()
    return {int(version): int(count) for version, count in rows}


def _topics_done(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str | bytes):  # MariaDB returns JSON columns as text
        value = json.loads(value or "[]")
    return len(value) if isinstance(value, list) else 0


def _status(row: Any) -> str:
    if row.passed_at is not None:
        return "completed"
    started = (row.sessions_count or 0) > 0 or row.first_started_at is not None
    return "in_progress" if started or _topics_done(row.topics_covered) else "not_started"


def _sort_key(card: dict[str, Any]) -> tuple[int, str, str]:
    open_and_required = card["is_required"] and not card["is_completed"]
    return (0 if open_and_required else 1, card["assigned_at"] or "", card["training_id"])


def training_cards(db: Session, uid: int, profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.execute(_listed(uid)).all()
    versions = {int(r.progress_version_id or r.active_version_id) for r in rows}
    topic_counts = _topic_counts(db, versions)
    cards = []
    for r in rows:
        version = int(r.progress_version_id or r.active_version_id)
        completed = r.passed_at is not None
        cards.append(
            {
                # Keys of Wanaka's procedure, in its order
                "tags": r.tags,
                "trainer_id": r.wanaka_trainer_id,
                "assigned_at": _wanaka_time(r.assigned_at),
                "trainer_key": r.training_id,
                "trainer_name": r.app_title or r.title,
                "trainer_category": r.category,
                "elevenlabs_agent_id": r.completion_key,
                "trainer_description": r.description,
                "trainer_person_name": profile.get("district_manager_name"),
                "trainer_picture": profile.get("district_manager_picture"),
                "matched_rule_group_id": r.matched_rule_group_id,
                "matched_rule_name": r.matched_rule_name,
                "ai_flag": r.ai_flag,
                "is_required": bool(r.is_required),
                "is_completed": completed,
                # New keys
                "training_id": r.training_id,
                "completion_type": r.completion_type,
                "status": _status(r),
                "progress": {
                    "topics_done": _topics_done(r.topics_covered),
                    "topics_total": topic_counts.get(version, 0),
                    "quiz_retry": bool(r.quiz_attempted) and not completed and r.completion_type == "quiz",
                },
                "due_at": _wanaka_time(r.due_at),
            }
        )
    return sorted(cards, key=_sort_key)


def training_list(db: Session, settings: Settings, uid: int, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": uid,
        "name": profile.get("name"),
        "job_id": profile.get("job_id"),
        "store_id": profile.get("store_id"),
        "job_title": profile.get("job_title"),
        "store_name": profile.get("store_name"),
        "market_name": profile.get("market_name"),
        "region_name": profile.get("region_name"),
        "district_name": profile.get("district_name"),
        # Wanaka used the month of its latest sales data; assignments here don't depend on it.
        "assignment_month": datetime.now(ZoneInfo(settings.default_timezone)).strftime("%Y-%m"),
        "assigned_trainers": training_cards(db, uid, profile),
    }


def pending_count(db: Session, uid: int, profile: dict[str, Any]) -> dict[str, Any]:
    cards = training_cards(db, uid, profile)
    return {
        "uid": uid,
        "name": profile.get("name"),
        "incompleted_count": sum(1 for c in cards if c["is_required"] and not c["is_completed"]),
    }


def listed_training(db: Session, uid: int, training_id: str) -> Any:
    """The row for one listed training, or None if it isn't in this employee's list."""
    return db.execute(_listed(uid).where(training_assignments.c.training_id == training_id)).first()
