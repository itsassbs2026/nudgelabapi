"""The figures every page uses (SPEC §5), defined once.

Two kinds of figures:
- Activity in the period: sessions, trainees, completions, ratings, review scores, cost. Each counts events
  whose own timestamp falls in the date range.
- Cohort progress (funnel, completion rate): a cohort of trainees and how far they've got so far. The cohort
  is trainees **assigned** in the period when assignment data exists for the scope (Wanaka sync), otherwise
  trainees who **started** in the period. `basis` says which, so the dashboard can label it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    session_reviews,
    session_usage,
    training_assignments,
    training_feedback,
    training_progress,
    training_sessions,
    trainings,
)
from app.reports.filters import ReportFilters, session_conditions, trainee_conditions, training_conditions


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal | int | float) else float(str(value))


def rounded(value: Any, places: int = 2) -> float | None:
    v = to_float(value)
    return None if v is None else round(v, places)


def ratio(part: int | float, whole: int | float) -> float | None:
    return round(part / whole, 4) if whole else None


def sessions_from() -> Any:
    """training_sessions joined to trainings (for completion-type filters)."""
    return training_sessions.outerjoin(trainings, trainings.c.training_id == training_sessions.c.training_id)


# -- activity ------------------------------------------------------------------------------------------------


@dataclass
class Activity:
    sessions: int = 0
    trainees: int = 0
    avg_session_seconds: float | None = None
    completions: int = 0
    avg_rating: float | None = None
    ratings: int = 0
    avg_review_score: float | None = None
    reviewed_sessions: int = 0
    flagged_sessions: int = 0
    cost: float = 0.0
    cost_per_completion: float | None = None


def activity(db: Session, f: ReportFilters) -> Activity:
    s = training_sessions.c
    cond = and_(*session_conditions(f))
    row = db.execute(
        select(func.count(), func.count(distinct(s.uid)), func.avg(s.duration_sec))
        .select_from(sessions_from())
        .where(cond)
    ).one()
    cost = db.execute(
        select(func.coalesce(func.sum(session_usage.c.est_total_cost), 0))
        .select_from(sessions_from().join(session_usage, session_usage.c.session_id == s.session_id))
        .where(cond)
    ).scalar_one()
    rating = db.execute(
        select(func.avg(training_feedback.c.rating), func.count(training_feedback.c.rating))
        .select_from(sessions_from().join(training_feedback, training_feedback.c.session_id == s.session_id))
        .where(cond)
    ).one()
    review = db.execute(
        select(
            func.avg(session_reviews.c.score),
            func.count(session_reviews.c.score),
            func.coalesce(func.sum(session_reviews.c.flagged), 0),
        )
        .select_from(sessions_from().join(session_reviews, session_reviews.c.session_id == s.session_id))
        .where(cond)
    ).one()
    completions = completions_in_period(db, f)
    a = Activity(
        sessions=int(row[0]),
        trainees=int(row[1]),
        avg_session_seconds=rounded(row[2], 0),
        completions=completions,
        avg_rating=rounded(rating[0], 2),
        ratings=int(rating[1]),
        avg_review_score=rounded(review[0], 2),
        reviewed_sessions=int(review[1]),
        flagged_sessions=int(review[2] or 0),
        cost=rounded(cost, 2) or 0.0,
    )
    a.cost_per_completion = rounded(a.cost / completions, 2) if completions else None
    return a


def completions_in_period(db: Session, f: ReportFilters) -> int:
    p = training_progress.c
    return int(
        db.execute(
            select(func.count()).where(
                p.passed_at >= f.start_utc,
                p.passed_at < f.end_utc,
                *training_conditions(f, p.training_id),
                *trainee_conditions(f, p.uid),
            )
        ).scalar_one()
    )


# -- cohort funnel -------------------------------------------------------------------------------------------


@dataclass
class Funnel:
    basis: str  # "assigned" or "started"
    cohort: int = 0
    started: int = 0
    walkthrough_done: int = 0
    quiz_attempted: int = 0
    completed: int = 0
    completion_rate: float | None = None


@dataclass
class CohortMember:
    uid: int
    training_id: str
    started: bool
    walkthrough_done: bool
    quiz_attempted: bool
    completed: bool
    topics_covered: list[int] = field(default_factory=list)


def assignments_exist(db: Session, f: ReportFilters) -> bool:
    a = training_assignments.c
    return bool(
        db.execute(
            select(func.count()).where(
                a.status != "cancelled",
                *training_conditions(f, a.training_id),
                *trainee_conditions(f, a.uid),
            )
        ).scalar_one()
    )


def cohort(db: Session, f: ReportFilters) -> tuple[str, list[CohortMember]]:
    """The cohort with each member's progress so far. Basis "assigned" if assignments exist for the scope."""
    p = training_progress.c
    if assignments_exist(db, f):
        a = training_assignments.c
        rows = db.execute(
            select(
                a.uid,
                a.training_id,
                p.first_started_at,
                p.walkthrough_finished_at,
                p.quiz_attempted,
                p.passed_at,
                p.topics_covered,
            )
            .select_from(
                training_assignments.outerjoin(
                    training_progress, and_(p.uid == a.uid, p.training_id == a.training_id)
                )
            )
            .where(
                a.status != "cancelled",
                a.assigned_at >= f.start_utc,
                a.assigned_at < f.end_utc,
                *training_conditions(f, a.training_id),
                *trainee_conditions(f, a.uid),
            )
        ).all()
        basis = "assigned"
    else:
        rows = db.execute(
            select(
                p.uid,
                p.training_id,
                p.first_started_at,
                p.walkthrough_finished_at,
                p.quiz_attempted,
                p.passed_at,
                p.topics_covered,
            ).where(
                p.first_started_at >= f.start_utc,
                p.first_started_at < f.end_utc,
                *training_conditions(f, p.training_id),
                *trainee_conditions(f, p.uid),
            )
        ).all()
        basis = "started"
    members = [
        CohortMember(
            uid=int(r[0]),
            training_id=str(r[1]),
            started=r[2] is not None,
            walkthrough_done=r[3] is not None,
            quiz_attempted=bool(r[4]),
            completed=r[5] is not None,
            topics_covered=[int(t) for t in (r[6] or [])],
        )
        for r in rows
    ]
    return basis, members


def funnel_of(basis: str, members: list[CohortMember]) -> Funnel:
    fn = Funnel(basis=basis, cohort=len(members))
    fn.started = sum(m.started for m in members)
    fn.walkthrough_done = sum(m.walkthrough_done for m in members)
    fn.quiz_attempted = sum(m.quiz_attempted for m in members)
    fn.completed = sum(m.completed for m in members)
    fn.completion_rate = ratio(fn.completed, fn.cohort)
    return fn


def funnel(db: Session, f: ReportFilters) -> Funnel:
    return funnel_of(*cohort(db, f))


# -- daily series --------------------------------------------------------------------------------------------


def local_day(value: datetime, tz: ZoneInfo) -> date:
    from datetime import UTC

    return value.replace(tzinfo=UTC).astimezone(tz).date()


def daily_series(db: Session, f: ReportFilters) -> list[dict[str, Any]]:
    """Sessions, completions and cost per local calendar day, every day in the range (zeros included)."""
    from datetime import timedelta

    tz = ZoneInfo(f.timezone)
    s = training_sessions.c
    days: dict[date, dict[str, Any]] = {}
    d = f.date_from
    while d <= f.date_to:
        days[d] = {"date": d.isoformat(), "sessions": 0, "completions": 0, "cost": 0.0}
        d += timedelta(days=1)
    for started_at, cost in db.execute(
        select(s.started_at, session_usage.c.est_total_cost)
        .select_from(sessions_from().outerjoin(session_usage, session_usage.c.session_id == s.session_id))
        .where(*session_conditions(f))
    ):
        day = days.get(local_day(started_at, tz))
        if day is not None:
            day["sessions"] += 1
            day["cost"] += to_float(cost) or 0.0
    p = training_progress.c
    for (passed_at,) in db.execute(
        select(p.passed_at).where(
            p.passed_at >= f.start_utc,
            p.passed_at < f.end_utc,
            *training_conditions(f, p.training_id),
            *trainee_conditions(f, p.uid),
        )
    ):
        day = days.get(local_day(passed_at, tz))
        if day is not None:
            day["completions"] += 1
    for day in days.values():
        day["cost"] = round(day["cost"], 2)
    return list(days.values())


def per_training_activity(db: Session, f: ReportFilters) -> dict[str, dict[str, Any]]:
    """Sessions, trainees, cost, average rating and review score per training, for the period."""
    s = training_sessions.c
    cond = and_(*session_conditions(f))
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for tid, n, people in db.execute(
        select(s.training_id, func.count(), func.count(distinct(s.uid)))
        .select_from(sessions_from())
        .where(cond)
        .group_by(s.training_id)
    ):
        out[tid].update(sessions=int(n), trainees=int(people))
    for tid, cost in db.execute(
        select(s.training_id, func.sum(session_usage.c.est_total_cost))
        .select_from(sessions_from().join(session_usage, session_usage.c.session_id == s.session_id))
        .where(cond)
        .group_by(s.training_id)
    ):
        out[tid]["cost"] = rounded(cost, 2)
    for tid, avg_rating in db.execute(
        select(s.training_id, func.avg(training_feedback.c.rating))
        .select_from(sessions_from().join(training_feedback, training_feedback.c.session_id == s.session_id))
        .where(cond)
        .group_by(s.training_id)
    ):
        out[tid]["avg_rating"] = rounded(avg_rating, 2)
    for tid, avg_score in db.execute(
        select(s.training_id, func.avg(session_reviews.c.score))
        .select_from(sessions_from().join(session_reviews, session_reviews.c.session_id == s.session_id))
        .where(cond)
        .group_by(s.training_id)
    ):
        out[tid]["avg_review_score"] = rounded(avg_score, 2)
    p = training_progress.c
    for tid, n in db.execute(
        select(p.training_id, func.count())
        .where(p.passed_at >= f.start_utc, p.passed_at < f.end_utc, *trainee_conditions(f, p.uid))
        .group_by(p.training_id)
    ):
        out[tid]["completions"] = int(n)
    return out
