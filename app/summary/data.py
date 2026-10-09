"""The figures in the daily summary email (docs/DAILY_SUMMARY.md): one local day, compared with the day before
and the 7 days before it.

Headline figures come from the same functions as the dashboard's Overview (app/reports/metrics.py), so the
email and the dashboard always agree. The "needs attention" counts are extra: calls that never connected,
people who tried several times in under a minute, early hang-ups, low ratings and overdue assignments. Test
calls (bots, previews) are left out, as everywhere in the reports.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    roleplay_attempts,
    training_assignments,
    training_feedback,
    training_progress,
    training_sessions,
    trainings,
)
from app.reports import metrics
from app.reports.filters import ReportFilters, build_filters, session_conditions

SHORT_CALL_SECONDS = 60  # a "try" that went nowhere
REPEAT_TRIES = 3  # this many short calls on one training in a day = someone stuck
EARLY_LEAVE_SECONDS = 120
LOW_RATING = 5  # out of 10


def _day(day: date, timezone: str) -> ReportFilters:
    return build_filters(timezone=timezone, date_from=day, date_to=day, today=day)


def _kpis(db: Session, f: ReportFilters) -> dict[str, Any]:
    a = metrics.activity(db, f)
    return {
        "calls": a.sessions,
        "people": a.trainees,
        "passed": a.completions,
        "pass_rate": metrics.ratio(a.completions, a.trainees),
        "avg_rating": a.avg_rating,
        "ratings": a.ratings,
        "cost": a.cost,
        "cost_per_completion": a.cost_per_completion,
        "flagged": a.flagged_sessions,
        "avg_minutes": round(a.avg_session_seconds / 60, 1) if a.avg_session_seconds else None,
    }


def _week_average(db: Session, day: date, timezone: str) -> dict[str, Any]:
    """Calls, people and passes per day, averaged over the 7 days before `day`."""
    f = build_filters(
        timezone=timezone, date_from=day - timedelta(days=7), date_to=day - timedelta(days=1), today=day
    )
    s = training_sessions.c
    calls, people = db.execute(
        select(func.count(), func.count(distinct(s.uid)))
        .select_from(metrics.sessions_from())
        .where(and_(*session_conditions(f)))
    ).one()
    return {
        "calls": round(int(calls) / 7, 1),
        "passed": round(metrics.completions_in_period(db, f) / 7, 1),
        "people_total": int(people),
    }


def _attention(db: Session, f: ReportFilters) -> dict[str, Any]:
    s = training_sessions.c
    cond = and_(*session_conditions(f))
    never_connected = db.execute(
        select(func.count())
        .select_from(metrics.sessions_from())
        .where(cond, s.duration_sec.is_(None), s.outcome.is_(None))
    ).scalar_one()
    early = db.execute(
        select(func.count())
        .select_from(metrics.sessions_from())
        .where(cond, s.outcome != "passed", s.duration_sec < EARLY_LEAVE_SECONDS)
    ).scalar_one()
    tries = (
        select(s.uid, s.training_id, func.count().label("n"))
        .select_from(metrics.sessions_from())
        .where(cond, s.duration_sec < SHORT_CALL_SECONDS, or_(s.outcome.is_(None), s.outcome != "passed"))
        .group_by(s.uid, s.training_id)
        .having(func.count() >= REPEAT_TRIES)
        .subquery()
    )
    stuck_people, stuck_calls = db.execute(
        select(func.count(distinct(tries.c.uid)), func.coalesce(func.sum(tries.c.n), 0))
    ).one()
    low = db.execute(
        select(func.count())
        .select_from(
            metrics.sessions_from().join(training_feedback, training_feedback.c.session_id == s.session_id)
        )
        .where(cond, training_feedback.c.rating <= LOW_RATING)
    ).scalar_one()
    a, p = training_assignments.c, training_progress.c
    overdue = db.execute(
        select(func.count())
        .select_from(
            training_assignments.outerjoin(
                training_progress, and_(p.uid == a.uid, p.training_id == a.training_id)
            )
        )
        .where(a.status != "cancelled", a.due_at.is_not(None), a.due_at < f.end_utc, p.passed_at.is_(None))
    ).scalar_one()
    return {
        "never_connected": int(never_connected),
        "early_leaves": int(early),
        "stuck_people": int(stuck_people),
        "stuck_calls": int(stuck_calls),
        "low_ratings": int(low),
        "overdue": int(overdue),
    }


def _by_training(db: Session, f: ReportFilters) -> list[dict[str, Any]]:
    """Yesterday's activity per training, with its overall progress (passed of assigned, to date)."""
    s = training_sessions.c
    activity = metrics.per_training_activity(db, f)
    minutes = dict(
        db.execute(
            select(s.training_id, func.avg(s.duration_sec))
            .select_from(metrics.sessions_from())
            .where(and_(*session_conditions(f)), s.duration_sec.is_not(None))
            .group_by(s.training_id)
        ).all()
    )
    a, p = training_assignments.c, training_progress.c
    overall = {
        tid: (int(assigned), int(passed or 0))
        for tid, assigned, passed in db.execute(
            select(a.training_id, func.count(), func.sum(p.passed_at.is_not(None)))
            .select_from(
                training_assignments.outerjoin(
                    training_progress, and_(p.uid == a.uid, p.training_id == a.training_id)
                )
            )
            .where(a.status != "cancelled")
            .group_by(a.training_id)
        )
    }
    titles = dict(db.execute(select(trainings.c.training_id, trainings.c.title)).all())
    rows = []
    for tid, act in activity.items():
        people, passed = act.get("trainees", 0), act.get("completions", 0)
        if not act.get("sessions"):
            continue
        assigned, passed_total = overall.get(tid, (0, 0))
        rows.append(
            {
                "training_id": tid,
                "title": titles.get(tid) or tid,
                "calls": act.get("sessions", 0),
                "people": people,
                "passed": passed,
                "pass_rate": metrics.ratio(passed, people),
                "avg_minutes": round(float(minutes[tid]) / 60, 1) if minutes.get(tid) else None,
                "avg_rating": act.get("avg_rating"),
                "assigned": assigned,
                "passed_to_date": passed_total,
                "overall_rate": metrics.ratio(passed_total, assigned),
            }
        )
    rows.sort(key=lambda r: (-r["people"], r["title"]))
    return rows


def _roleplay(db: Session, f: ReportFilters) -> dict[str, Any] | None:
    r = roleplay_attempts.c
    n, people, avg = db.execute(
        select(func.count(), func.count(distinct(r.uid)), func.avg(r.score)).where(
            r.started_at >= f.start_utc, r.started_at < f.end_utc, r.score.is_not(None)
        )
    ).one()
    if not n:
        return None
    return {"practices": int(n), "people": int(people), "avg_score": metrics.rounded(avg, 1)}


def daily_summary(db: Session, day: date, timezone: str) -> dict[str, Any]:
    """Everything the email shows for `day` (a local calendar day in `timezone`)."""
    f = _day(day, timezone)
    yesterday = _kpis(db, f)
    before = _kpis(db, _day(day - timedelta(days=1), timezone))
    return {
        "day": day.isoformat(),
        "timezone": timezone,
        "kpis": yesterday,
        "day_before": before,
        "week_average": _week_average(db, day, timezone),
        "attention": _attention(db, f),
        "trainings": _by_training(db, f),
        "roleplay": _roleplay(db, f),
    }
