"""Session list and session viewer data (SPEC §7.2, §8.2).

The viewer gets everything in one call: metadata, the transcript (with `seconds_into_session` for seeking), a
timeline of events (topics reached, quiz grades, safety corrections, refused hang-ups, errors, the
acknowledgment and the rating), the AI review with each issue linked to a transcript line, and usage and cost.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.reference.agent_tables import (
    quiz_answers,
    session_issues,
    session_reviews,
    session_topic_events,
    session_transcripts,
    session_usage,
    training_acknowledgments,
    training_feedback,
    training_profiles,
    training_sessions,
    training_topics,
    training_versions,
    training_voices,
    trainings,
    vw_trainees,
    vw_training_stores,
)
from app.reports import metrics, quality
from app.reports.filters import ReportFilters, session_conditions
from app.services import recordings
from app.utils.errors import ApiError

OUTCOMES = ("passed", "not_passed", "no_quiz")
END_REASONS = ("completed", "user_left", "error", "dropped")


def recording_state(settings: Settings, key: str | None, started_at: datetime) -> str:
    """available | none (never recorded) | expired (deleted by the bucket's lifecycle rule)."""
    if not key:
        return "none"
    return "expired" if recordings.is_expired(settings, started_at) else "available"


# -- list ----------------------------------------------------------------------------------------------------


def session_list(
    db: Session,
    settings: Settings,
    f: ReportFilters,
    *,
    uid: int | None = None,
    outcome: str | None = None,
    end_reason: str | None = None,
    flagged: bool | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    s = training_sessions.c
    st = vw_training_stores.c
    fb = training_feedback.c
    rv = session_reviews.c
    conditions: list[ColumnElement[bool]] = list(session_conditions(f))
    if uid is not None:
        conditions.append(s.uid == uid)
    if outcome:
        conditions.append(s.outcome == outcome)
    if end_reason:
        conditions.append(s.end_reason == end_reason)
    if flagged is not None:
        is_flagged: ColumnElement[bool] = func.coalesce(rv.flagged, 0) == (1 if flagged else 0)
        conditions.append(is_flagged)
    if min_rating is not None:
        conditions.append(fb.rating >= min_rating)
    if max_rating is not None:
        conditions.append(fb.rating <= max_rating)
    if search:
        term = search.strip()
        like = f"%{term}%"
        options: list[ColumnElement[bool]] = [vw_trainees.c.name.like(like), s.session_id.like(f"{term}%")]
        if term.isdigit():
            options.append(s.uid == int(term))
        conditions.append(or_(*options))

    # Count and page on the sessions table with only the joins the filters need, then look up the extras for
    # the page's rows alone. Joining every session in range to the people and store views was quadratic at
    # full rollout: v_stores_all.store_id is utf8mb3 and store_id_at_session utf8mb4, so MySQL can't use the
    # store index for that join (Phase 17 load test: minutes for 180,000 sessions).
    source = metrics.sessions_from()
    if min_rating is not None or max_rating is not None:
        source = source.outerjoin(training_feedback, fb.session_id == s.session_id)
    if flagged is not None:
        source = source.outerjoin(session_reviews, rv.session_id == s.session_id)
    if search:
        source = source.outerjoin(vw_trainees, vw_trainees.c.uid == s.uid)
    where = and_(*conditions)
    total = int(db.execute(select(func.count()).select_from(source).where(where)).scalar_one())
    page_ids = list(
        db.execute(
            select(s.session_id)
            .select_from(source)
            .where(where)
            .order_by(s.started_at.desc(), s.session_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    rows = (
        db.execute(
            select(
                s.session_id,
                s.started_at,
                s.duration_sec,
                s.uid,
                s.store_id_at_session,
                s.training_id,
                trainings.c.title,
                s.outcome,
                s.end_reason,
                s.client,
                fb.rating,
                rv.score,
                rv.flagged,
                session_usage.c.est_total_cost,
                s.recording_s3_key,
            )
            .select_from(
                metrics.sessions_from()
                .outerjoin(training_feedback, fb.session_id == s.session_id)
                .outerjoin(session_reviews, rv.session_id == s.session_id)
                .outerjoin(session_usage, session_usage.c.session_id == s.session_id)
            )
            .where(s.session_id.in_(page_ids))
            .order_by(s.started_at.desc(), s.session_id)
        ).all()
        if page_ids
        else []
    )
    uids = {r.uid for r in rows if r.uid is not None}
    names = (
        dict(
            db.execute(select(vw_trainees.c.uid, vw_trainees.c.name).where(vw_trainees.c.uid.in_(uids))).all()
        )
        if uids
        else {}
    )
    store_ids = {r.store_id_at_session for r in rows if r.store_id_at_session}
    stores = (
        dict(db.execute(select(st.store_id, st.store_name).where(st.store_id.in_(store_ids))).all())
        if store_ids
        else {}
    )
    return {
        "items": [
            {
                "session_id": r.session_id,
                "started_at": r.started_at,
                "duration_sec": r.duration_sec,
                "uid": r.uid,
                "name": names.get(r.uid),
                "store_id": r.store_id_at_session,
                "store_name": stores.get(r.store_id_at_session),
                "training_id": r.training_id,
                "training_title": r.title,
                "outcome": r.outcome,
                "end_reason": r.end_reason,
                "client": r.client,
                "rating": r.rating,
                "review_score": r.score,
                "flagged": bool(r.flagged),
                "cost": metrics.rounded(r.est_total_cost, 4),
                "recording": recording_state(settings, r.recording_s3_key, r.started_at),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# -- detail --------------------------------------------------------------------------------------------------


def _seconds(at: datetime | None, started_at: datetime) -> int | None:
    return None if at is None else max(0, int((at - started_at).total_seconds()))


_WORDS = re.compile(r"[a-z0-9']+")


def _words(text: str) -> str:
    return " ".join(_WORDS.findall(text.lower()))


def _clock_seconds(clock: str, started_at: datetime, duration: int | None) -> int | None:
    """The review's "HH:MM:SS" (the agent server's UTC clock) as seconds into the session, if inside it."""
    try:
        t = time.fromisoformat(clock.strip())
    except (ValueError, AttributeError):
        return None
    at = datetime.combine(started_at.date(), t)
    if at < started_at - timedelta(minutes=1):
        at += timedelta(days=1)  # the session crossed midnight UTC
    seconds = int((at - started_at).total_seconds())
    limit = (duration or 0) + 120
    return max(seconds, 0) if -60 <= seconds <= limit else None


def link_issues(
    issues: list[dict[str, Any]], transcript: list[dict[str, Any]], started_at: datetime, duration: int | None
) -> list[dict[str, Any]]:
    """Attach `seq` (the transcript line the issue quotes) and `seconds` to each review issue."""
    lines = [(line["seq"], _words(line["message"]), line["seconds"]) for line in transcript]
    out = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        quote = _words(str(issue.get("quote") or ""))
        seq = seconds = None
        if quote:
            for line_seq, words, line_seconds in lines:
                if quote in words:
                    seq, seconds = line_seq, line_seconds
                    break
        if seconds is None and issue.get("time"):
            seconds = _clock_seconds(str(issue["time"]), started_at, duration)
        out.append(
            {
                "type": issue.get("type"),
                "time": issue.get("time"),
                "quote": issue.get("quote"),
                "detail": issue.get("detail"),
                "seq": seq,
                "seconds": seconds,
            }
        )
    return out


def session_detail(db: Session, settings: Settings, session_id: str) -> dict[str, Any]:
    s = training_sessions.c
    st = vw_training_stores.c
    head = db.execute(
        select(
            training_sessions,
            vw_trainees.c.name.label("trainee_name"),
            trainings.c.title.label("training_title"),
            trainings.c.completion_type,
            training_versions.c.version_label,
            training_profiles.c.display_name.label("profile_name"),
            training_voices.c.display_name.label("voice_name"),
            st.store_name,
            st.district_name,
            st.market_name,
            st.region_name,
        )
        .select_from(
            training_sessions.outerjoin(trainings, trainings.c.training_id == s.training_id)
            .outerjoin(training_versions, training_versions.c.version_id == s.version_id)
            .outerjoin(vw_trainees, vw_trainees.c.uid == s.uid)
            .outerjoin(training_profiles, training_profiles.c.profile_id == s.profile_id)
            .outerjoin(training_voices, training_voices.c.voice_id == s.voice_id)
            .outerjoin(vw_training_stores, st.store_id == s.store_id_at_session)
        )
        .where(s.session_id == session_id)
    ).first()
    if head is None:
        raise ApiError(404, "not_found", "Session not found.")
    started = head.started_at

    tr = session_transcripts.c
    transcript = [
        {
            "seq": int(r.seq),
            "role": r.role,
            "message": r.message,
            "seconds": int(r.seconds_into_session or 0),
            "interrupted": bool(r.interrupted),
        }
        for r in db.execute(select(session_transcripts).where(tr.session_id == session_id).order_by(tr.seq))
    ]

    events: list[dict[str, Any]] = []
    te = session_topic_events.c
    topic_titles: dict[int, str] = {
        int(n): str(title)
        for n, title in db.execute(
            select(training_topics.c.topic_number, training_topics.c.title).where(
                training_topics.c.version_id == head.version_id
            )
        )
    }
    for r in db.execute(select(session_topic_events).where(te.session_id == session_id)):
        events.append(
            {
                "type": "topic_reached",
                "at": r.reached_at,
                "seconds": _seconds(r.reached_at, started),
                "label": f"Topic {r.topic_number}: {topic_titles.get(r.topic_number, '')}".rstrip(": "),
                "data": {"topic_number": r.topic_number},
            }
        )
    qa = quiz_answers.c
    for r in db.execute(select(quiz_answers).where(qa.session_id == session_id)):
        events.append(
            {
                "type": "quiz_answer",
                "at": r.answered_at,
                "seconds": _seconds(r.answered_at, started),
                "label": f"Q{r.question_number}: {r.given_option} ({'right' if r.is_correct else 'wrong'})",
                "data": {
                    "question_number": r.question_number,
                    "round": r.round,
                    "given_option": r.given_option,
                    "is_correct": bool(r.is_correct),
                    "heard_as": r.heard_as,
                },
            }
        )
    si = session_issues.c
    for r in db.execute(select(session_issues).where(si.session_id == session_id)):
        events.append(
            {
                "type": r.issue_type,
                "at": r.occurred_at,
                "seconds": _seconds(r.occurred_at, started),
                "label": {
                    "guardrail": "Safety check corrected Anne",
                    "end_call_refused": "Hang-up refused",
                    "error": "Error",
                    "dropped": "Connection dropped",
                    "reconnected": "Reconnected",
                    "not_reconnected": "Didn't reconnect",
                    "no_trainee": "Trainee never joined",
                    "no_response": "No reply, call ended",
                }.get(r.issue_type, r.issue_type),
                "data": {"detail": r.detail},
            }
        )
    ack = db.execute(
        select(training_acknowledgments).where(training_acknowledgments.c.session_id == session_id)
    ).first()
    if ack is not None:
        events.append(
            {
                "type": "acknowledged",
                "at": ack.acknowledged_at,
                "seconds": _seconds(ack.acknowledged_at, started),
                "label": "Acknowledged",
                "data": {"statement": ack.statement, "trainee_quote": ack.trainee_quote},
            }
        )
    fb = db.execute(select(training_feedback).where(training_feedback.c.session_id == session_id)).first()
    if fb is not None and fb.created_at is not None:
        events.append(
            {
                "type": "rating",
                "at": fb.created_at,
                "seconds": _seconds(fb.created_at, started),
                "label": f"Rated {fb.rating}" if fb.rating is not None else "Feedback",
                "data": {"rating": fb.rating},
            }
        )
    events.sort(key=lambda e: (e["at"] is None, e["at"] or started))

    rv = db.execute(select(session_reviews).where(session_reviews.c.session_id == session_id)).first()
    review = None
    if rv is not None:
        review = {
            "score": rv.score,
            "summary": rv.summary,
            "flagged": bool(rv.flagged),
            "reviewed_at": rv.reviewed_at,
            "model": rv.review_model,
            "issues": link_issues(rv.issues or [], transcript, started, head.duration_sec),
        }

    us = db.execute(select(session_usage).where(session_usage.c.session_id == session_id)).first()
    usage = None
    if us is not None:
        usage = {
            "llm_model": us.llm_model,
            "llm_requests": us.llm_requests,
            "llm_input_tokens": us.llm_input_tokens,
            "llm_cached_tokens": us.llm_cached_tokens,
            "llm_cache_write_tokens": us.llm_cache_write_tokens,
            "llm_output_tokens": us.llm_output_tokens,
            "tts_characters": us.tts_characters,
            "stt_audio_seconds": metrics.to_float(us.stt_audio_seconds),
            "recorded_seconds": us.recorded_seconds,
            "cost": {
                "claude": metrics.rounded(us.est_llm_cost, 4),
                "polly": metrics.rounded(us.est_tts_cost, 4),
                "transcribe": metrics.rounded(us.est_stt_cost, 4),
                "total": metrics.rounded(us.est_total_cost, 4),
            },
        }

    return {
        "session": {
            "session_id": head.session_id,
            "uid": head.uid,
            "trainee_name": head.trainee_name,
            "job_title_at_session": head.job_title_at_session,
            "training_id": head.training_id,
            "training_title": head.training_title,
            "completion_type": head.completion_type,
            "version_id": head.version_id,
            "version_label": head.version_label,
            "store": {
                "store_id": head.store_id_at_session,
                "store_name": head.store_name,
                "district_name": head.district_name,
                "market_name": head.market_name,
                "region_name": head.region_name,
            },
            "started_at": started,
            "ended_at": head.ended_at,
            "duration_sec": head.duration_sec,
            "start_point": head.start_point,
            "outcome": head.outcome,
            "end_reason": head.end_reason,
            "client": head.client,
            "profile_id": head.profile_id,
            "profile_name": head.profile_name,
            "voice_id": head.voice_id,
            "voice_name": head.voice_name,
            "llm_model": head.llm_model,
            "agent_version": head.agent_version,
            "summary": head.summary,
            "recording": recording_state(settings, head.recording_s3_key, started),
        },
        "transcript": transcript,
        "events": events,
        "review": review,
        "feedback": None
        if fb is None
        else {"rating": fb.rating, "comment": fb.comment, "trainee_quote": fb.trainee_quote},
        "usage": usage,
        "quality": quality.queue_state(db, session_id),
    }


def recording_key(db: Session, settings: Settings, session_id: str) -> str:
    """The S3 key to play, or the reason there isn't one (404 / 410)."""
    row = db.execute(
        select(training_sessions.c.recording_s3_key, training_sessions.c.started_at).where(
            training_sessions.c.session_id == session_id
        )
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "Session not found.")
    state = recording_state(settings, row.recording_s3_key, row.started_at)
    if state == "none":
        raise ApiError(404, "recording_missing", "This session wasn't recorded.")
    if state == "expired":
        raise ApiError(
            410,
            "recording_expired",
            f"Recordings are kept for {settings.recording_retention_days} days; this one has been deleted.",
        )
    return str(row.recording_s3_key)
