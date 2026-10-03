"""Trainings table, Training detail and question statistics (SPEC §7.2)."""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    EXCLUDED_CLIENTS,
    quiz_answers,
    session_reviews,
    session_topic_events,
    training_feedback,
    training_progress,
    training_questions,
    training_sessions,
    training_topics,
    training_versions,
    trainings,
    vw_trainees,
)
from app.reports import metrics
from app.reports.filters import ReportFilters, session_conditions, trainee_conditions
from app.utils.errors import ApiError


def _training_row(db: Session, training_id: str) -> Any:
    row = db.execute(select(trainings).where(trainings.c.training_id == training_id)).one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "Training not found.")
    return row


def _active_label(db: Session, version_id: int | None) -> str | None:
    if version_id is None:
        return None
    return db.execute(
        select(training_versions.c.version_label).where(training_versions.c.version_id == version_id)
    ).scalar_one_or_none()


def trainings_table(db: Session, f: ReportFilters) -> list[dict[str, Any]]:
    """One row per training: type, active version, cohort progress and period activity."""
    q = select(trainings).order_by(trainings.c.title)
    if f.training_id:
        q = q.where(trainings.c.training_id == f.training_id)
    if f.completion_type:
        q = q.where(trainings.c.completion_type == f.completion_type)
    activity = metrics.per_training_activity(db, f)
    out = []
    for t in db.execute(q).all():
        fn = metrics.funnel(db, replace(f, training_id=t.training_id))
        act = activity.get(t.training_id, {})
        out.append(
            {
                "training_id": t.training_id,
                "title": t.title,
                "status": t.status,
                "completion_type": t.completion_type,
                "active_version": _active_label(db, t.active_version_id),
                "funnel": dataclasses.asdict(fn),
                "sessions": act.get("sessions", 0),
                "trainees": act.get("trainees", 0),
                "completions": act.get("completions", 0),
                "avg_rating": act.get("avg_rating"),
                "avg_review_score": act.get("avg_review_score"),
                "cost": act.get("cost") or 0.0,
            }
        )
    return out


def _topic_titles(db: Session, version_id: int | None) -> dict[int, str]:
    if version_id is None:
        return {}
    return {
        int(n): str(title)
        for n, title in db.execute(
            select(training_topics.c.topic_number, training_topics.c.title).where(
                training_topics.c.version_id == version_id
            )
        )
    }


def dropoff(
    members: list[metrics.CohortMember], completion_type: str, topic_count: int
) -> list[dict[str, Any]]:
    """Where cohort members who haven't completed stopped: the topic they never reached, or the last step."""
    stops: Counter[str] = Counter()
    for m in members:
        if m.completed:
            continue
        if not m.started:
            stops["not_started"] += 1
        elif m.walkthrough_done or (topic_count and max(m.topics_covered, default=0) >= topic_count):
            stops[
                "quiz"
                if completion_type == "quiz"
                else "acknowledgment"
                if completion_type == "acknowledgment"
                else "final_topic"
            ] += 1
        else:
            stops[f"topic_{max(m.topics_covered, default=0) + 1}"] += 1
    return [{"stage": k, "trainees": v} for k, v in stops.items()]


def time_per_topic(db: Session, f: ReportFilters) -> dict[int, dict[str, Any]]:
    """Seconds from reaching topic N to reaching topic N+1, within the same session (median and average)."""
    s = training_sessions.c
    e = session_topic_events.c
    by_session: dict[str, list[tuple[int, datetime]]] = defaultdict(list)
    for sid, n, reached in db.execute(
        select(e.session_id, e.topic_number, e.reached_at)
        .select_from(
            session_topic_events.join(training_sessions, s.session_id == e.session_id).outerjoin(
                trainings, trainings.c.training_id == s.training_id
            )
        )
        .where(*session_conditions(f))
    ):
        by_session[sid].append((int(n), reached))
    spans: dict[int, list[float]] = defaultdict(list)
    for events in by_session.values():
        events.sort()
        for (n, t0), (n1, t1) in zip(events, events[1:], strict=False):
            if n1 == n + 1 and t1 >= t0:
                spans[n].append((t1 - t0).total_seconds())
    return {
        n: {"median_seconds": round(median(v)), "avg_seconds": round(sum(v) / len(v)), "samples": len(v)}
        for n, v in spans.items()
    }


def attempts(
    db: Session, f: ReportFilters, training_id: str, members: list[metrics.CohortMember]
) -> dict[str, list[dict[str, int]]]:
    """Retries per trainee (SPEC §7.2), for the same cohort as the funnel: how many quiz rounds each one has
    started, and how many sessions they've had for this training so far (test sessions left out)."""
    uids = sorted({m.uid for m in members})
    if not uids:
        return {"quiz_rounds": [], "sessions_per_trainee": []}
    qa = quiz_answers.c
    rounds = Counter(
        int(n)
        for (n,) in db.execute(
            select(func.max(qa.round)).where(qa.training_id == training_id, qa.uid.in_(uids)).group_by(qa.uid)
        )
    )
    s = training_sessions.c
    not_bots = [] if f.include_bots else [or_(s.client.is_(None), s.client.not_in(EXCLUDED_CLIENTS))]
    sessions = Counter(
        int(n)
        for (n,) in db.execute(
            select(func.count())
            .where(s.training_id == training_id, s.uid.in_(uids), *not_bots)
            .group_by(s.uid)
        )
    )
    return {
        "quiz_rounds": [{"rounds": k, "trainees": rounds[k]} for k in sorted(rounds)],
        "sessions_per_trainee": [{"sessions": k, "trainees": sessions[k]} for k in sorted(sessions)],
    }


def training_detail(db: Session, f: ReportFilters, training_id: str) -> dict[str, Any]:
    t = _training_row(db, training_id)
    tf = replace(f, training_id=training_id)
    basis, members = metrics.cohort(db, tf)
    fn = metrics.funnel_of(basis, members)
    titles = _topic_titles(db, t.active_version_id)
    timing = time_per_topic(db, tf)
    topics = [
        {"topic_number": n, "title": titles.get(n), **(timing.get(n) or {})}
        for n in sorted(set(titles) | set(timing))
    ]

    s = training_sessions.c
    cond = and_(*session_conditions(tf))
    ratings = Counter(
        {
            int(r): int(c)
            for r, c in db.execute(
                select(training_feedback.c.rating, func.count())
                .select_from(
                    training_sessions.join(
                        training_feedback, training_feedback.c.session_id == s.session_id
                    ).outerjoin(trainings, trainings.c.training_id == s.training_id)
                )
                .where(cond, training_feedback.c.rating.is_not(None))
                .group_by(training_feedback.c.rating)
            )
        }
    )
    comments = [
        {
            "session_id": sid,
            "uid": uid,
            "name": name,
            "rating": rating,
            "comment": comment or quote,
            "created_at": created,
        }
        for sid, uid, name, rating, comment, quote, created in db.execute(
            select(
                training_feedback.c.session_id,
                training_feedback.c.uid,
                vw_trainees.c.name,
                training_feedback.c.rating,
                training_feedback.c.comment,
                training_feedback.c.trainee_quote,
                training_feedback.c.created_at,
            )
            .select_from(
                training_sessions.join(training_feedback, training_feedback.c.session_id == s.session_id)
                .outerjoin(trainings, trainings.c.training_id == s.training_id)
                .outerjoin(vw_trainees, vw_trainees.c.uid == training_feedback.c.uid)
            )
            .where(cond)
            .order_by(training_feedback.c.created_at.desc())
            .limit(20)
        )
    ]
    scores: Counter[int] = Counter()
    issue_types: Counter[str] = Counter()
    for score, issues in db.execute(
        select(session_reviews.c.score, session_reviews.c.issues)
        .select_from(
            training_sessions.join(session_reviews, session_reviews.c.session_id == s.session_id).outerjoin(
                trainings, trainings.c.training_id == s.training_id
            )
        )
        .where(cond)
    ):
        if score is not None:
            scores[int(score)] += 1
        for issue in issues or []:
            if isinstance(issue, dict) and issue.get("type"):
                issue_types[str(issue["type"])] += 1

    p = training_progress.c
    versions = []
    for v in db.execute(
        select(training_versions)
        .where(training_versions.c.training_id == training_id)
        .order_by(training_versions.c.version_id.desc())
    ).all():
        sessions_n = db.execute(
            select(func.count())
            .select_from(training_sessions.outerjoin(trainings, trainings.c.training_id == s.training_id))
            .where(cond, s.version_id == v.version_id)
        ).scalar_one()
        completions_n = db.execute(
            select(func.count()).where(
                p.training_id == training_id,
                p.version_id == v.version_id,
                p.passed_at >= f.start_utc,
                p.passed_at < f.end_utc,
                *trainee_conditions(f, p.uid),
            )
        ).scalar_one()
        versions.append(
            {
                "version_id": v.version_id,
                "label": v.version_label,
                "published_at": v.published_at,
                "is_active": v.version_id == t.active_version_id,
                "sessions": int(sessions_n),
                "completions": int(completions_n),
            }
        )

    act = metrics.activity(db, tf)
    return {
        "attempts": attempts(db, tf, training_id, members),
        "training_id": t.training_id,
        "title": t.title,
        "completion_type": t.completion_type,
        "status": t.status,
        "active_version": _active_label(db, t.active_version_id),
        "funnel": dataclasses.asdict(fn),
        "activity": dataclasses.asdict(act),
        "dropoff": dropoff(members, t.completion_type, len(titles)),
        "topics": topics,
        "ratings": {
            "distribution": {str(k): ratings[k] for k in sorted(ratings)},
            "count": sum(ratings.values()),
            "average": act.avg_rating,
            "comments": comments,
        },
        "reviews": {
            "distribution": {str(k): scores[k] for k in sorted(scores)},
            "average": act.avg_review_score,
            "flagged": act.flagged_sessions,
            "top_issue_types": [{"type": k, "count": v} for k, v in issue_types.most_common(10)],
        },
        "versions": versions,
    }


def question_stats(db: Session, f: ReportFilters, training_id: str) -> dict[str, Any]:
    """Per question: first-try accuracy (each trainee's first answer), overall accuracy, the most common wrong
    option and examples of what was heard. Most-missed first."""
    t = _training_row(db, training_id)
    tf = replace(f, training_id=training_id)
    q = training_questions.c
    texts: dict[int, dict[str, Any]] = {}
    if t.active_version_id is not None:
        for row in db.execute(
            select(
                q.question_number,
                q.location_variant,
                q.section_code,
                q.section_name,
                q.question_text,
                q.options,
                q.correct_option,
            )
            .where(q.version_id == t.active_version_id)
            .order_by(q.question_number, q.location_variant)
        ):
            texts.setdefault(
                int(row.question_number),
                {
                    "section_code": row.section_code,
                    "section_name": row.section_name,
                    "question": row.question_text,
                    "options": row.options,
                    "correct_option": row.correct_option,
                },
            )

    s = training_sessions.c
    a = quiz_answers.c
    answers = db.execute(
        select(a.uid, a.question_number, a.given_option, a.is_correct, a.heard_as, a.answered_at)
        .select_from(
            quiz_answers.join(training_sessions, s.session_id == a.session_id).outerjoin(
                trainings, trainings.c.training_id == s.training_id
            )
        )
        .where(
            *session_conditions(tf, in_range=False), a.answered_at >= f.start_utc, a.answered_at < f.end_utc
        )
        .order_by(a.answered_at)
    ).all()
    first: dict[tuple[int, int], bool] = {}
    per_q: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"answers": 0, "correct": 0, "wrong": Counter(), "heard": []}
    )
    for uid, number, given, correct, heard, _at in answers:
        stats = per_q[int(number)]
        stats["answers"] += 1
        stats["correct"] += int(bool(correct))
        first.setdefault((int(uid), int(number)), bool(correct))
        if not correct:
            stats["wrong"][given] += 1
            if heard:
                stats["heard"].append(heard)
    out = []
    for number in sorted(set(texts) | set(per_q)):
        st = per_q.get(number, {"answers": 0, "correct": 0, "wrong": Counter(), "heard": []})
        firsts = [ok for (_, n), ok in first.items() if n == number]
        wrong = st["wrong"].most_common(1)
        out.append(
            {
                "question_number": number,
                **texts.get(number, {}),
                "trainees": len(firsts),
                "first_try_accuracy": metrics.ratio(sum(firsts), len(firsts)),
                "answers": st["answers"],
                "overall_accuracy": metrics.ratio(st["correct"], st["answers"]),
                "most_common_wrong_option": wrong[0][0] if wrong else None,
                "heard_examples": st["heard"][-3:],
            }
        )
    out.sort(
        key=lambda r: (r["first_try_accuracy"] is None, r["first_try_accuracy"] or 0, r["question_number"])
    )
    return {"training_id": training_id, "title": t.title, "questions": out}
