"""Employee detail, the feedback list and the compliance (acknowledgment) list (SPEC §7.2)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    EXCLUDED_CLIENTS,
    quiz_answers,
    session_reviews,
    session_usage,
    training_acknowledgments,
    training_assignments,
    training_feedback,
    training_progress,
    training_sessions,
    trainings,
    vw_trainees,
)
from app.reports import metrics
from app.reports.filters import ReportFilters, session_conditions, trainee_conditions, training_conditions
from app.reports.search import like_pattern
from app.utils.errors import ApiError


def employee(db: Session, uid: int, *, include_bots: bool = False) -> dict[str, Any]:
    """Everything about one trainee, all time (not limited to the date range)."""
    t = vw_trainees.c
    header = db.execute(select(vw_trainees).where(t.uid == uid)).one_or_none()
    p = training_progress.c
    progress_rows = db.execute(
        select(training_progress, trainings.c.title, trainings.c.completion_type)
        .select_from(training_progress.outerjoin(trainings, trainings.c.training_id == p.training_id))
        .where(p.uid == uid)
        .order_by(p.updated_at.desc())
    ).all()
    if header is None and not progress_rows:
        raise ApiError(404, "not_found", "Employee not found.")

    s = training_sessions.c
    session_q = (
        select(
            s.session_id,
            s.training_id,
            trainings.c.title,
            s.started_at,
            s.duration_sec,
            s.start_point,
            s.outcome,
            s.end_reason,
            s.client,
            s.profile_id,
            s.voice_id,
            s.recording_s3_key,
            session_usage.c.est_total_cost,
            training_feedback.c.rating,
            session_reviews.c.score,
            session_reviews.c.flagged,
        )
        .select_from(
            training_sessions.outerjoin(trainings, trainings.c.training_id == s.training_id)
            .outerjoin(session_usage, session_usage.c.session_id == s.session_id)
            .outerjoin(training_feedback, training_feedback.c.session_id == s.session_id)
            .outerjoin(session_reviews, session_reviews.c.session_id == s.session_id)
        )
        .where(s.uid == uid)
        .order_by(s.started_at.desc())
        .limit(200)
    )
    if not include_bots:
        session_q = session_q.where(or_(s.client.is_(None), s.client.not_in(EXCLUDED_CLIENTS)))
    sessions = [
        {
            "session_id": r[0],
            "training_id": r[1],
            "training_title": r[2],
            "started_at": r[3],
            "duration_sec": r[4],
            "start_point": r[5],
            "outcome": r[6],
            "end_reason": r[7],
            "client": r[8],
            "profile_id": r[9],
            "voice_id": r[10],
            "has_recording": bool(r[11]),
            "cost": metrics.rounded(r[12], 3),
            "rating": r[13],
            "review_score": r[14],
            "flagged": bool(r[15]) if r[15] is not None else None,
        }
        for r in db.execute(session_q)
    ]

    a = quiz_answers.c
    answers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in db.execute(
        select(
            a.training_id, a.question_number, a.round, a.given_option, a.is_correct, a.heard_as, a.answered_at
        )
        .where(a.uid == uid)
        .order_by(a.answered_at)
    ):
        answers[r.training_id].append(
            {
                "question_number": r.question_number,
                "round": r.round,
                "given_option": r.given_option,
                "is_correct": bool(r.is_correct),
                "heard_as": r.heard_as,
                "answered_at": r.answered_at,
            }
        )
    k = training_acknowledgments.c
    acks = [
        {
            "training_id": r.training_id,
            "session_id": r.session_id,
            "statement": r.statement,
            "trainee_quote": r.trainee_quote,
            "acknowledged_at": r.acknowledged_at,
        }
        for r in db.execute(
            select(training_acknowledgments).where(k.uid == uid).order_by(k.acknowledged_at.desc())
        )
    ]
    return {
        "uid": uid,
        "name": header.name if header else None,
        "is_active": bool(header.is_active) if header else None,
        "job_title": header.job_title if header else None,
        "store": None
        if header is None
        else {
            "store_id": header.store_id,
            "store_name": header.store_name,
            "district_id": header.district_id,
            "district_name": header.district_name,
            "market_id": header.market_id,
            "market_name": header.market_name,
            "region_id": header.region_id,
            "region_name": header.region_name,
        },
        "trainings": [
            {
                "training_id": r.training_id,
                "title": r.title,
                "completion_type": r.completion_type,
                "status": r.status,
                "topics_covered": r.topics_covered or [],
                "walkthrough_finished_at": r.walkthrough_finished_at,
                "quiz_attempted": bool(r.quiz_attempted),
                "correct_questions": r.correct_questions or [],
                "sessions_count": r.sessions_count,
                "first_started_at": r.first_started_at,
                "completed_at": r.passed_at,
                "answers": answers.get(r.training_id, []),
            }
            for r in progress_rows
        ],
        "sessions": sessions,
        "acknowledgments": acks,
    }


def feedback_list(
    db: Session,
    f: ReportFilters,
    *,
    search: str | None,
    min_rating: int | None,
    max_rating: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    s = training_sessions.c
    fb = training_feedback.c
    q = (
        select(
            fb.session_id,
            fb.uid,
            vw_trainees.c.name,
            vw_trainees.c.store_name,
            fb.training_id,
            trainings.c.title,
            fb.rating,
            fb.comment,
            fb.trainee_quote,
            fb.created_at,
        )
        .select_from(
            training_sessions.join(training_feedback, fb.session_id == s.session_id)
            .outerjoin(trainings, trainings.c.training_id == s.training_id)
            .outerjoin(vw_trainees, vw_trainees.c.uid == fb.uid)
        )
        .where(*session_conditions(f))
    )
    if min_rating is not None:
        q = q.where(fb.rating >= min_rating)
    if max_rating is not None:
        q = q.where(fb.rating <= max_rating)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(fb.comment.like(like), fb.trainee_quote.like(like), vw_trainees.c.name.like(like)))
    total = int(db.execute(select(func.count()).select_from(q.subquery())).scalar_one())
    rows = db.execute(q.order_by(fb.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [
            {
                "session_id": r[0],
                "uid": r[1],
                "name": r[2],
                "store_name": r[3],
                "training_id": r[4],
                "training_title": r[5],
                "rating": r[6],
                "comment": r[7] or r[8],
                "created_at": r[9],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def acknowledgments_list(db: Session, f: ReportFilters, *, page: int, page_size: int) -> dict[str, Any]:
    k = training_acknowledgments.c
    q = (
        select(
            k.ack_id,
            k.uid,
            vw_trainees.c.name,
            vw_trainees.c.store_name,
            k.training_id,
            trainings.c.title,
            k.statement,
            k.trainee_quote,
            k.acknowledged_at,
            k.session_id,
        )
        .select_from(
            training_acknowledgments.outerjoin(trainings, trainings.c.training_id == k.training_id).outerjoin(
                vw_trainees, vw_trainees.c.uid == k.uid
            )
        )
        .where(
            and_(
                k.acknowledged_at >= f.start_utc,
                k.acknowledged_at < f.end_utc,
                *training_conditions(f, k.training_id),
                *trainee_conditions(f, k.uid),
            )
        )
    )
    total = int(db.execute(select(func.count()).select_from(q.subquery())).scalar_one())
    rows = db.execute(
        q.order_by(k.acknowledged_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "ack_id": r[0],
                "uid": r[1],
                "name": r[2],
                "store_name": r[3],
                "training_id": r[4],
                "training_title": r[5],
                "statement": r[6],
                "trainee_quote": r[7],
                "acknowledged_at": r[8],
                "session_id": r[9],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


ASSIGNMENT_STATES = ("not_started", "in_progress", "completed", "overdue")
DUE_SOON_DAYS = 7


def assignments_list(
    db: Session,
    f: ReportFilters,
    *,
    state: str | None,
    page: int,
    page_size: int,
    job_title: str | None = None,
    search: str | None = None,
    any_date: bool = False,
    active_only: bool = False,
) -> dict[str, Any]:
    """Assignments with each trainee's progress, for the Assignments page and its export.

    State: completed (passed), overdue (not passed and past due), in progress (started), or not started; plus
    `due_soon` (not passed, due within the next 7 days), counted separately because it overlaps the others.
    By default only assignments made in the period are listed (the "assigned" funnel's cohort); `any_date`
    lists every current one. Place in the company (region to store) and job title are the trainee's current
    ones (vw_trainees).
    """
    a = training_assignments.c
    p = training_progress.c
    t = vw_trainees.c
    now = func.utc_timestamp()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    not_passed = p.passed_at.is_(None)
    overdue = and_(not_passed, a.due_at.is_not(None), a.due_at < now)
    due_soon = and_(not_passed, a.due_at >= now_utc, a.due_at < now_utc + timedelta(days=DUE_SOON_DAYS))
    state_col = case(
        (p.passed_at.is_not(None), "completed"),
        (overdue, "overdue"),
        (p.first_started_at.is_not(None), "in_progress"),
        else_="not_started",
    )
    conditions: list[ColumnElement[bool]] = [
        a.status != "cancelled",
        *training_conditions(f, a.training_id),
        *trainee_conditions(f, a.uid),
    ]
    if not any_date:
        conditions += [a.assigned_at >= f.start_utc, a.assigned_at < f.end_utc]
    if job_title:
        conditions.append(t.job_title == job_title)
    if active_only:
        conditions.append(t.is_active == 1)
    if search and search.strip():
        term = search.strip()
        options: list[ColumnElement[bool]] = [t.name.like(like_pattern(term))]
        if term.isdigit():
            options.append(a.uid == int(term))
        conditions.append(or_(*options))
    base = (
        select(
            a.assignment_id,
            a.assigned_via,
            a.uid,
            t.name,
            t.job_title,
            t.is_active,
            t.store_id,
            t.store_name,
            t.district_name,
            t.market_name,
            t.region_name,
            a.training_id,
            trainings.c.title,
            a.assigned_at,
            a.due_at,
            state_col.label("state"),
            case((due_soon, 1), else_=0).label("due_soon"),
            p.sessions_count,
            p.first_started_at,
            p.passed_at,
        )
        .select_from(
            training_assignments.outerjoin(
                training_progress, and_(p.uid == a.uid, p.training_id == a.training_id)
            )
            .outerjoin(trainings, trainings.c.training_id == a.training_id)
            .outerjoin(vw_trainees, t.uid == a.uid)
        )
        .where(and_(*conditions))
    ).subquery()
    counts = {s: 0 for s in (*ASSIGNMENT_STATES, "due_soon")}
    for value, n, soon_n in db.execute(
        select(base.c.state, func.count(), func.sum(base.c.due_soon)).group_by(base.c.state)
    ):
        counts[str(value)] = int(n)
        counts["due_soon"] += int(soon_n or 0)
    q = select(base)
    if state == "due_soon":
        q = q.where(base.c.due_soon == 1)
    elif state:
        q = q.where(base.c.state == state)
    total = int(db.execute(select(func.count()).select_from(q.subquery())).scalar_one())
    rows = db.execute(
        q.order_by(base.c.due_at.is_(None), base.c.due_at, base.c.assigned_at.desc(), base.c.uid)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "counts": counts,
        "items": [
            {
                "assignment_id": r.assignment_id,
                "assigned_via": r.assigned_via,
                "uid": r.uid,
                "name": r.name,
                "job_title": r.job_title,
                "is_active": None if r.is_active is None else bool(r.is_active),
                "store_id": r.store_id,
                "store_name": r.store_name,
                "district_name": r.district_name,
                "market_name": r.market_name,
                "region_name": r.region_name,
                "training_id": r.training_id,
                "training_title": r.title,
                "assigned_at": r.assigned_at,
                "due_at": r.due_at,
                "state": r.state,
                "due_soon": bool(r.due_soon),
                "sessions": int(r.sessions_count or 0),
                "started_at": r.first_started_at,
                "completed_at": r.passed_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def rating_trend(db: Session, f: ReportFilters) -> dict[str, Any]:
    """Average rating per training per week (weeks start on Monday, in the user's time zone), for the
    Feedback page's trend chart. Same sessions as the feedback list: the period, the filters, no test
    calls."""
    s = training_sessions.c
    fb = training_feedback.c
    tz = ZoneInfo(f.timezone)
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    titles: dict[str, str | None] = {}
    for training_id, title, rating, created_at, started_at in db.execute(
        select(s.training_id, trainings.c.title, fb.rating, fb.created_at, s.started_at)
        .select_from(
            training_sessions.join(training_feedback, fb.session_id == s.session_id).outerjoin(
                trainings, trainings.c.training_id == s.training_id
            )
        )
        .where(fb.rating.is_not(None), *session_conditions(f))
    ):
        day = metrics.local_day(created_at or started_at, tz)
        week = (day - timedelta(days=day.weekday())).isoformat()
        buckets[(week, training_id)].append(int(rating))
        titles[training_id] = title
    points = [
        {
            "week": week,
            "training_id": training_id,
            "title": titles.get(training_id),
            "average": round(sum(ratings) / len(ratings), 2),
            "count": len(ratings),
        }
        for (week, training_id), ratings in sorted(buckets.items())
    ]
    return {"points": points}
