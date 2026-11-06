"""What one training's last week of calls says about its content: the figures and quotes Claude works from.

Everything here is read from the agent's tables (sessions, quiz answers, topic events, ratings,
transcripts) and the live version's content. Transcripts are sampled (a busy training has thousands of calls
a week), and every quote carries its session id, so a recommendation can point at real calls.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    quiz_answers,
    session_topic_events,
    session_transcripts,
    training_feedback,
    training_sessions,
    training_topics,
    training_versions,
    trainings,
)
from app.reports.filters import ReportFilters, session_conditions
from app.reports.trainings import question_stats, time_per_topic

SAMPLE_SESSIONS = 250  # transcripts read for trainee questions and "say that again" moments
MAX_QUOTES = 30
MAX_CONTENT_CHARS = 40_000
QUESTIONISH = re.compile(
    r"\?|^(what|how|why|when|where|who|which|can|could|does|do|is|are|should|will|would)\b", re.I
)
CONFUSED = re.compile(
    r"\b(repeat (that|the|it)|say (that|it) again|didn'?t (hear|catch|understand|get)|i don'?t understand|"
    r"confus|lost me|too (fast|long)|can i see|show me|pull up|look at (it|that|the))\b",
    re.I,
)


def _sessions(db: Session, f: ReportFilters) -> list[Any]:
    s = training_sessions.c
    return list(
        db.execute(
            select(s.session_id, s.uid, s.outcome, s.end_reason, s.duration_sec, s.version_id)
            .select_from(training_sessions.outerjoin(trainings, trainings.c.training_id == s.training_id))
            .where(*session_conditions(f))
        )
    )


def _ratings(db: Session, ids: list[str]) -> list[Any]:
    fb = training_feedback.c
    out: list[Any] = []
    for chunk in range(0, len(ids), 1000):
        out += db.execute(
            select(fb.session_id, fb.rating, fb.trainee_quote).where(
                fb.session_id.in_(ids[chunk : chunk + 1000]), fb.rating.is_not(None)
            )
        ).all()
    return out


def _hangups(
    db: Session, f: ReportFilters, left: list[str], topic_titles: dict[int, str]
) -> list[dict[str, Any]]:
    """Calls that ended with the trainee leaving before passing: the last topic they reached, or the quiz."""
    if not left:
        return []
    te, qa = session_topic_events.c, quiz_answers.c
    last_topic: dict[str, int] = {}
    in_quiz: set[str] = set()
    for chunk in range(0, len(left), 1000):
        part = left[chunk : chunk + 1000]
        for sid, n in db.execute(
            select(te.session_id, func.max(te.topic_number))
            .where(te.session_id.in_(part))
            .group_by(te.session_id)
        ):
            last_topic[sid] = int(n)
        in_quiz |= {r[0] for r in db.execute(select(qa.session_id).where(qa.session_id.in_(part)).distinct())}
    where: Counter[str] = Counter()
    for sid in left:
        if sid in in_quiz:
            where["during the quiz"] += 1
        elif sid in last_topic:
            n = last_topic[sid]
            where[f"topic {n}: {topic_titles.get(n, '')}".rstrip(": ")] += 1
        else:
            where["before the first topic (welcome)"] += 1
    return [{"where": k, "calls": v} for k, v in where.most_common(8)]


def _never_spoke(db: Session, ids: list[str]) -> set[str]:
    """The calls among ids with no trainee line in the transcript."""
    tr = session_transcripts.c
    spoke: set[str] = set()
    for chunk in range(0, len(ids), 1000):
        part = ids[chunk : chunk + 1000]
        spoke |= {
            r[0]
            for r in db.execute(
                select(tr.session_id).where(tr.session_id.in_(part), tr.role == "trainee").distinct()
            )
        }
    return set(ids) - spoke


def _wrong_heard(db: Session, f: ReportFilters, numbers: list[int]) -> dict[int, list[dict[str, Any]]]:
    """For the most-missed questions: what trainees actually said when graded wrong (up to 8 each)."""
    if not numbers:
        return {}
    s, qa = training_sessions.c, quiz_answers.c
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for number, given, heard, sid in db.execute(
        select(qa.question_number, qa.given_option, qa.heard_as, qa.session_id)
        .select_from(quiz_answers.join(training_sessions, s.session_id == qa.session_id))
        .where(
            s.training_id == f.training_id,
            qa.answered_at >= f.start_utc,
            qa.answered_at < f.end_utc,
            qa.question_number.in_(numbers),
            qa.is_correct.is_(False),
            qa.heard_as.is_not(None),
        )
        .order_by(qa.answered_at.desc())
        .limit(400)
    ):
        if len(out[int(number)]) < 8:
            out[int(number)].append({"graded_as": given, "said": heard, "session_id": sid})
    return out


def _trainee_lines(db: Session, ids: list[str]) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    """(questions trainees asked, "say that again"/"what does that mean" moments, how many of those) in a
    sample."""
    if not ids:
        return [], [], 0
    rng = random.Random(len(ids))  # the same sample for the same set of calls
    sample = rng.sample(ids, min(SAMPLE_SESSIONS, len(ids)))
    tr = session_transcripts.c
    asked: list[dict[str, str]] = []
    confused: list[dict[str, str]] = []
    confused_count = 0
    for sid, message in db.execute(
        select(tr.session_id, tr.message)
        .where(tr.session_id.in_(sample), tr.role == "trainee")
        .order_by(tr.seq)
    ):
        text = " ".join(str(message or "").split())
        if not text:
            continue
        if CONFUSED.search(text):
            confused_count += 1
            if len(confused) < MAX_QUOTES:
                confused.append({"said": text[:240], "session_id": sid})
        elif QUESTIONISH.search(text) and len(text.split()) >= 4 and len(asked) < MAX_QUOTES:
            asked.append({"said": text[:240], "session_id": sid})
    return asked, confused, confused_count


def _content(db: Session, version_id: int | None) -> str:
    if version_id is None:
        return ""
    raw = db.execute(
        select(training_versions.c.content).where(training_versions.c.version_id == version_id)
    ).scalar_one_or_none()
    if raw is None:
        return ""
    content = json.loads(raw) if isinstance(raw, str | bytes) else raw
    return json.dumps(content, ensure_ascii=False)[:MAX_CONTENT_CHARS]


def gather(db: Session, f: ReportFilters) -> dict[str, Any]:
    """The evidence for f.training_id over f's dates. `calls` is 0 when there were none."""
    t = db.execute(
        select(trainings.c.title, trainings.c.completion_type, trainings.c.active_version_id).where(
            trainings.c.training_id == f.training_id
        )
    ).one()
    rows = _sessions(db, f)
    ids = [r.session_id for r in rows]
    base: dict[str, Any] = {
        "training_id": f.training_id,
        "title": t.title,
        "completion_type": t.completion_type,
        "live_version_id": t.active_version_id,
        "window": {"from": f.date_from.isoformat(), "to": f.date_to.isoformat(), "timezone": f.timezone},
        "calls": len(ids),
    }
    if not ids:
        return base
    people = {r.uid for r in rows}
    passed = {r.uid for r in rows if r.outcome == "passed"}
    durations = [int(r.duration_sec) for r in rows if r.duration_sec]
    ratings = _ratings(db, ids)
    values = [int(r.rating) for r in ratings]
    low = [r for r in ratings if r.rating <= 5 and r.trainee_quote]
    high = [r for r in ratings if r.rating >= 8 and r.trainee_quote]
    rng = random.Random(len(ids))
    quotes = [
        {
            "rating": int(r.rating),
            "said": " ".join(str(r.trainee_quote).split())[:240],
            "session_id": r.session_id,
        }
        for r in rng.sample(low, min(MAX_QUOTES, len(low))) + rng.sample(high, min(8, len(high)))
    ]

    topic_titles = {
        int(n): str(title)
        for n, title in db.execute(
            select(training_topics.c.topic_number, training_topics.c.title).where(
                training_topics.c.version_id == t.active_version_id
            )
        )
    }
    left = [r.session_id for r in rows if r.end_reason == "user_left" and r.outcome != "passed"]
    # 2026-11-06: of 634 Benefits calls "left before the first topic", 364 never said a word (repeated Start
    # taps, mic or connection problems). Those aren't about the welcome, so they're counted apart.
    silent = _never_spoke(db, left)
    left = [sid for sid in left if sid not in silent]
    questions = question_stats(db, f, str(f.training_id))["questions"]
    most_missed = [
        q["question_number"]
        for q in questions
        if q.get("first_try_accuracy") is not None and q["first_try_accuracy"] < 0.85
    ][:6]
    heard = _wrong_heard(db, f, most_missed)
    for q in questions:
        q.pop("heard_examples", None)
        if q["question_number"] in heard:
            q["wrong_answers_heard"] = heard[q["question_number"]]
    s, qa = training_sessions.c, quiz_answers.c
    retried = db.execute(
        select(func.count(func.distinct(qa.uid)))
        .select_from(quiz_answers.join(training_sessions, s.session_id == qa.session_id))
        .where(s.session_id.in_(ids[:5000]), qa.round > 1)
    ).scalar_one()
    topics = time_per_topic(db, f)
    asked, confused, confused_count = _trainee_lines(db, ids)

    return {
        **base,
        "people": len(people),
        "people_passed": len(passed),
        "pass_rate": round(len(passed) / len(people), 2),
        "people_needing_a_review_round": int(retried or 0),
        "median_call_minutes": round(median(durations) / 60, 1) if durations else None,
        "ratings": {
            "count": len(values),
            "average": round(sum(values) / len(values), 2) if values else None,
            "1_to_4": sum(1 for v in values if v <= 4),
            "5_to_7": sum(1 for v in values if 5 <= v <= 7),
            "8_to_10": sum(1 for v in values if v >= 8),
        },
        "rating_quotes": quotes,
        "hangups_before_passing": _hangups(db, f, left, topic_titles),
        "left_without_saying_anything": {
            "calls": len(silent),
            "note": "The trainee never spoke: app, microphone or connection problems, not the content.",
        },
        "topics": [
            {"topic": n, "title": topic_titles.get(n), **topics.get(n, {})} for n in sorted(topic_titles)
        ],
        "questions_most_missed_first": questions,
        "transcript_sample": {
            "calls_sampled": min(SAMPLE_SESSIONS, len(ids)),
            "confused_or_repeat_requests": confused_count,
            "confused_examples": confused,
            "questions_trainees_asked": asked,
        },
        "content": _content(db, t.active_version_id),
    }
