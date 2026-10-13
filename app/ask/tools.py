"""The data Ask NudgeLab can look at (docs/ASK_NUDGELAB.md): read-only tools, each a wrapper around a report
the dashboard already shows, so the assistant's numbers match the pages. Results are trimmed to what an
answer needs.

Every tool takes plain JSON from the model; a bad value gives `{"error": ...}` back to the model (it can fix
its call), never an exception.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.reference.agent_tables import training_versions, trainings
from app.reports import metrics
from app.reports.filters import ReportFilters, build_filters
from app.reports.people import employee, feedback_list
from app.reports.roleplay import roleplay_report
from app.reports.sessions import session_detail, session_list
from app.reports.trainings import question_stats, trainings_table
from app.utils.errors import ApiError

MAX_CONTENT_CHARS = 24_000

_PERIOD = {
    "date_from": {
        "type": "string",
        "description": "First day, YYYY-MM-DD (local). Default: 6 days before date_to.",
    },
    "date_to": {"type": "string", "description": "Last day, YYYY-MM-DD (local), included. Default: today."},
}
_TRAINING = {"training_id": {"type": "string", "description": "A training id from the trainings tool."}}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "trainings",
        "description": "Every training (id, title, type, status) with calls, people, passes in the period, "
        "and "
        "overall progress (passed of assigned). Use it first to find a training's id from its name.",
        "input_schema": {"type": "object", "properties": {**_PERIOD}},
    },
    {
        "name": "activity",
        "description": "Calls, people, passes, average rating, flagged calls, cost and a series by day for "
        "the "
        "period, for all trainings or one.",
        "input_schema": {"type": "object", "properties": {**_PERIOD, **_TRAINING}},
    },
    {
        "name": "quiz_questions",
        "description": "A training's quiz questions: first-try accuracy, the wrong option most chosen and "
        "examples "
        "of what trainees said. Most-missed first.",
        "input_schema": {
            "type": "object",
            "properties": {**_PERIOD, **_TRAINING},
            "required": ["training_id"],
        },
    },
    {
        "name": "feedback",
        "description": "Trainees' ratings (1-10) and comments, newest first, with names and links to the "
        "calls. "
        "Filter by training, words in the comment, and rating range.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_PERIOD,
                **_TRAINING,
                "search": {"type": "string", "description": "Words to find in comments or names."},
                "min_rating": {"type": "integer"},
                "max_rating": {"type": "integer"},
                "limit": {"type": "integer", "description": "At most 100 (default 50)."},
            },
        },
    },
    {
        "name": "sessions",
        "description": "Calls in the period, newest first: who, training, length, outcome (passed, "
        "not_passed, "
        "no_quiz), why it ended, rating, with links. Filter by training, outcome, rating or a name.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_PERIOD,
                **_TRAINING,
                "outcome": {"type": "string", "enum": ["passed", "not_passed", "no_quiz"]},
                "min_rating": {"type": "integer"},
                "max_rating": {"type": "integer"},
                "search": {"type": "string", "description": "A trainee's name or uid."},
                "limit": {"type": "integer", "description": "At most 50 (default 20)."},
            },
        },
    },
    {
        "name": "transcript",
        "description": "What was said in one call (trainer and trainee lines in order), with its outcome and "
        "rating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "max_lines": {"type": "integer", "description": "At most 200 (default 80)."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "training_content",
        "description": "A training's live version: its lines (welcome, closing...), opening steps, topics "
        "(what "
        "the trainer says and asks) and quiz. Use it to point content advice at real text.",
        "input_schema": {"type": "object", "properties": {**_TRAINING}, "required": ["training_id"]},
    },
    {
        "name": "person",
        "description": "One trainee by uid: their trainings and progress, and their calls.",
        "input_schema": {"type": "object", "properties": {"uid": {"type": "integer"}}, "required": ["uid"]},
    },
    {
        "name": "roleplay",
        "description": "Role Play practices: per track, people, average Win Meter score (1-5) and "
        "how many "
        "opened the quiz; then each practice with the grader's strength, gap and tip.",
        "input_schema": {
            "type": "object",
            "properties": {**_PERIOD, **_TRAINING, "track": {"type": "string"}},
        },
    },
]

STATUS = {
    "trainings": "Looking at the trainings",
    "activity": "Counting calls and passes",
    "quiz_questions": "Checking the quiz questions",
    "feedback": "Reading feedback",
    "sessions": "Finding calls",
    "transcript": "Reading a call",
    "training_content": "Reading the training's content",
    "person": "Looking up the trainee",
    "roleplay": "Reading Role Play practices",
}


def _filters(args: dict[str, Any], timezone: str, today: date) -> ReportFilters:
    def day(name: str) -> date | None:
        value = args.get(name)
        return date.fromisoformat(str(value)) if value else None

    date_to = day("date_to") or today
    date_from = day("date_from") or (date_to - timedelta(days=6))
    return build_filters(
        timezone=timezone,
        date_from=date_from,
        date_to=date_to,
        training_id=args.get("training_id") or None,
        today=today,
    )


def _limit(args: dict[str, Any], default: int, most: int) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), most))
    except (TypeError, ValueError):
        return default


def _period(f: ReportFilters) -> dict[str, str]:
    return {"from": f.date_from.isoformat(), "to": f.date_to.isoformat(), "timezone": f.timezone}


def _trainings(db: Session, f: ReportFilters) -> dict[str, Any]:
    rows = trainings_table(db, f)
    return {
        "period": _period(f),
        "trainings": [
            {
                "training_id": r["training_id"],
                "title": r["title"],
                "type": r["completion_type"] or "quiz",
                "status": r["status"],
                "calls": r["sessions"],
                "people": r["trainees"],
                "passes": r["completions"],
                "avg_rating": r["avg_rating"],
                "overall": r["funnel"],
                "link": f"/trainings/{r['training_id']}",
            }
            for r in rows
        ],
    }


def _activity(db: Session, f: ReportFilters) -> dict[str, Any]:
    return {
        "period": _period(f),
        "training_id": f.training_id,
        **dataclasses.asdict(metrics.activity(db, f)),
        "by_day": metrics.daily_series(db, f),
    }


def _feedback(db: Session, f: ReportFilters, args: dict[str, Any]) -> dict[str, Any]:
    page = feedback_list(
        db,
        f,
        search=args.get("search") or None,
        min_rating=args.get("min_rating"),
        max_rating=args.get("max_rating"),
        page=1,
        page_size=_limit(args, 50, 100),
    )
    return {
        "period": _period(f),
        "total": page["total"],
        "items": [
            {
                "name": i.get("name"),
                "uid": i.get("uid"),
                "training": i.get("training_title") or i.get("training_id"),
                "rating": i.get("rating"),
                "comment": i.get("comment"),
                "when": i.get("created_at"),
                "link": f"/sessions/{i['session_id']}",
            }
            for i in page["items"]
        ],
    }


def _sessions(db: Session, settings: Settings, f: ReportFilters, args: dict[str, Any]) -> dict[str, Any]:
    page = session_list(
        db,
        settings,
        f,
        outcome=args.get("outcome") or None,
        min_rating=args.get("min_rating"),
        max_rating=args.get("max_rating"),
        search=args.get("search") or None,
        page=1,
        page_size=_limit(args, 20, 50),
    )
    keep = (
        "session_id",
        "started_at",
        "duration_sec",
        "name",
        "uid",
        "store_name",
        "training_title",
        "outcome",
        "end_reason",
        "rating",
        "flagged",
    )
    return {
        "period": _period(f),
        "total": page["total"],
        "items": [
            {**{k: i.get(k) for k in keep}, "link": f"/sessions/{i['session_id']}"} for i in page["items"]
        ],
    }


def _transcript(db: Session, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    detail = session_detail(db, settings, str(args["session_id"]))
    most = max(1, min(int(args.get("max_lines") or 80), 200))
    s = detail["session"]
    lines = detail["transcript"]
    return {
        "session": {
            k: s.get(k)
            for k in (
                "session_id",
                "training_title",
                "trainee_name",
                "started_at",
                "duration_sec",
                "outcome",
                "end_reason",
            )
        },
        "feedback": detail.get("feedback"),
        "lines": [
            {"who": "trainer" if ln["role"] == "trainer" else "trainee", "said": ln["message"]}
            for ln in lines[:most]
        ],
        "more_lines": max(0, len(lines) - most),
        "link": f"/sessions/{s.get('session_id')}",
    }


def _content(db: Session, training_id: str) -> dict[str, Any]:
    row = db.execute(
        select(
            trainings.c.title,
            trainings.c.completion_type,
            training_versions.c.version_id,
            training_versions.c.content,
        )
        .select_from(
            trainings.join(training_versions, training_versions.c.version_id == trainings.c.active_version_id)
        )
        .where(trainings.c.training_id == training_id)
    ).one_or_none()
    if row is None or row.content is None:
        return {"error": f"No live version with content for {training_id}."}
    content = json.loads(row.content) if isinstance(row.content, str | bytes) else row.content
    text = json.dumps(content, ensure_ascii=False)
    return {
        "training_id": training_id,
        "title": row.title,
        "live_version": row.version_id,
        "content": text[:MAX_CONTENT_CHARS],
        "truncated": len(text) > MAX_CONTENT_CHARS,
        "studio_link": f"/studio/versions/{row.version_id}",
    }


def run(db: Session, settings: Settings, name: str, args: dict[str, Any], timezone: str, today: date) -> Any:
    """One tool call; a mistake in the model's arguments comes back as {"error": ...} for it to fix."""
    try:
        if name in ("trainings", "activity", "quiz_questions", "feedback", "sessions", "roleplay"):
            f = _filters(args, timezone, today)
        if name == "trainings":
            return _trainings(db, f)
        if name == "activity":
            return _activity(db, f)
        if name == "quiz_questions":
            return {"period": _period(f), **question_stats(db, f, str(args["training_id"]))}
        if name == "feedback":
            return _feedback(db, f, args)
        if name == "sessions":
            return _sessions(db, settings, f, args)
        if name == "transcript":
            return _transcript(db, settings, args)
        if name == "training_content":
            return _content(db, str(args["training_id"]))
        if name == "person":
            return {**employee(db, int(args["uid"])), "link": f"/employees/{int(args['uid'])}"}
        if name == "roleplay":
            report = roleplay_report(db, f, track=args.get("track") or None, tier=None, page=1, page_size=30)
            return {"period": _period(f), **report}
        return {"error": f"There's no tool called {name}."}
    except ApiError as e:
        return {"error": e.message}
    except (KeyError, ValueError, TypeError) as e:
        return {"error": f"Bad arguments for {name}: {e}"}
