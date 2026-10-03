"""A small, fixed dataset in the agent's tables, for checking every report figure by hand.

`insert` fills any NOT NULL column without a default with a harmless placeholder, so rows only list what a
test cares about (the synced v_* tables have dozens of required columns).

The dataset (September 2026, all times UTC; the report time zone is America/Chicago):

    Region 1 "West" → market 10 "Texas" → district 100 "D-100" → stores S1, S2
    Region 2 "East" → market 20 "Florida" → district 200 "D-200" → store S3
    Trainees: 1001 (S1), 1002 (S1), 1003 (S2), 1004 (S3), 1005 (S3, assigned only)

    big4 (quiz, 3 topics, 2 questions):  1001 passed 09-06 · 1002 stopped after topic 1 · 1003 failed the quiz
    walk (walkthrough, 2 topics):        1004 completed 09-15 · 1001 completed 08-20 (before the range)

    Sessions in range: s1, s2 (1001), s3 (1002), s4 (1003), s5 (1004) = 5, cost 1.50
    Excluded: s6 bot test (cost 5.00), s7 in August, s8 at 03:00 UTC on 09-01 = 22:00 on 08-31 in Chicago
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, Numeric, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

_required_cache: dict[str, list[tuple[str, Any]]] = {}


def _placeholder(col: dict[str, Any]) -> Any:
    t = col["type"]
    if isinstance(t, DateTime):
        return dt.datetime(2000, 1, 1)
    if isinstance(t, Date):
        return dt.date(2000, 1, 1)
    if isinstance(t, Integer | Numeric):
        return 0
    if isinstance(t, JSON):
        return "[]"
    enums = getattr(t, "enums", None)
    if enums:
        return enums[0]
    return ""


def insert(db: Session, table: str, **values: Any) -> None:
    conn: Connection = db.connection()
    if table not in _required_cache:
        cols = inspect(conn).get_columns(table)
        _required_cache[table] = [
            (c["name"], c)
            for c in cols
            if not c["nullable"]
            and c.get("default") is None
            and not c.get("autoincrement")
            and not c.get("computed")
        ]
    row = dict(values)
    for name, col in _required_cache[table]:
        row.setdefault(name, _placeholder(col))
    names = ", ".join(f"`{k}`" for k in row)
    params = ", ".join(f":{k}" for k in row)
    conn.execute(text(f"INSERT INTO `{table}` ({names}) VALUES ({params})"), row)


def T(day: int, hour: int = 15, minute: int = 0, second: int = 0, month: int = 9) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, minute, second)


def seed(db: Session) -> None:
    stores = [
        ("S1", 1, "West", 10, "Texas", 100, "D-100"),
        ("S2", 1, "West", 10, "Texas", 100, "D-100"),
        ("S3", 2, "East", 20, "Florida", 200, "D-200"),
    ]
    for sid, rid, rname, mid, mname, did, dname in stores:
        insert(
            db,
            "v_stores_all",
            store_id=sid,
            store_name=f"Store {sid}",
            store_active=1,
            region_id=rid,
            region_name=rname,
            market_id=mid,
            market_name=mname,
            district_id=did,
            district_name=dname,
        )
    for uid, sid in ((1001, "S1"), (1002, "S1"), (1003, "S2"), (1004, "S3"), (1005, "S3")):
        insert(db, "v_users_all", uid=uid, name=f"Trainee {uid}", status=1, store_id=sid, job_title="RSC")

    insert(
        db,
        "training_profiles",
        profile_id="standard",
        display_name="Standard",
        llm_model="haiku",
        llm_input_per_m=1,
        llm_cached_per_m=0.1,
        llm_cache_write_per_m=1.25,
        llm_output_per_m=5,
        tts_per_m_chars=30,
        stt_per_minute=0.024,
    )
    insert(
        db,
        "training_voices",
        voice_id="Matthew",
        display_name="Matthew",
        language_code="en-US",
        gender="Male",
    )
    for tid, title, ctype in (("big4", "The Big 4", "quiz"), ("walk", "Store Safety", "walkthrough")):
        insert(db, "trainings", training_id=tid, title=title, status="active", completion_type=ctype)
    insert(
        db, "training_versions", version_id=1, training_id="big4", version_label="v1", content_hash="a" * 64
    )
    insert(
        db, "training_versions", version_id=2, training_id="walk", version_label="v1", content_hash="b" * 64
    )
    db.connection().execute(text("UPDATE trainings SET active_version_id = 1 WHERE training_id = 'big4'"))
    db.connection().execute(text("UPDATE trainings SET active_version_id = 2 WHERE training_id = 'walk'"))
    for n in (1, 2, 3):
        insert(db, "training_topics", version_id=1, topic_number=n, title=f"Big 4 topic {n}")
    for n in (1, 2):
        insert(db, "training_topics", version_id=2, topic_number=n, title=f"Safety topic {n}")
    for n, correct in ((1, "A"), (2, "B")):
        insert(
            db,
            "training_questions",
            version_id=1,
            question_number=n,
            location_variant="",
            section_code="A",
            section_name="Section",
            question_text=f"Question {n}?",
            options='{"A": "a", "B": "b", "C": "c"}',
            correct_option=correct,
        )

    def progress(
        uid: int,
        tid: str,
        started: dt.datetime,
        topics: str,
        wt: dt.datetime | None,
        quiz: int,
        passed: dt.datetime | None,
        version: int,
    ) -> None:
        insert(
            db,
            "training_progress",
            uid=uid,
            training_id=tid,
            version_id=version,
            topics_covered=topics,
            correct_questions="[]",
            first_started_at=started,
            walkthrough_finished_at=wt,
            quiz_attempted=quiz,
            passed_at=passed,
            status="passed" if passed else "in_progress",
        )

    progress(1001, "big4", T(5), "[1, 2, 3]", T(5, 15, 5), 1, T(6, 15, 4), 1)
    progress(1002, "big4", T(10), "[1]", None, 0, None, 1)
    progress(1003, "big4", T(12), "[1, 2, 3]", T(12, 15, 6), 1, None, 1)
    progress(1004, "walk", T(15), "[1, 2]", T(15, 15, 1), 0, T(15, 15, 2), 2)
    progress(1001, "walk", T(20, month=8), "[1, 2]", T(20, month=8), 0, T(20, month=8), 2)

    def session(
        sid: str,
        uid: int,
        tid: str,
        started: dt.datetime,
        dur: int,
        store: str,
        cost: str,
        client: str = "web_test",
        version: int = 1,
    ) -> None:
        insert(
            db,
            "training_sessions",
            session_id=sid,
            uid=uid,
            training_id=tid,
            version_id=version,
            room_name=f"room-{sid}",
            started_at=started,
            duration_sec=dur,
            start_point="new",
            store_id_at_session=store,
            client=client,
            profile_id="standard",
            voice_id="Matthew",
        )
        total = Decimal(cost)
        insert(
            db,
            "session_usage",
            session_id=sid,
            llm_model="haiku",
            llm_input_tokens=1000,
            llm_cached_tokens=800,
            llm_output_tokens=100,
            tts_characters=1000,
            stt_audio_seconds=60,
            est_llm_cost=total * Decimal("0.2"),
            est_tts_cost=total * Decimal("0.6"),
            est_stt_cost=total * Decimal("0.2"),
            est_total_cost=total,
        )

    session("s1", 1001, "big4", T(5), 600, "S1", "0.50")
    session("s2", 1001, "big4", T(6), 300, "S1", "0.30")
    session("s3", 1002, "big4", T(10), 200, "S1", "0.20")
    session("s4", 1003, "big4", T(12), 400, "S2", "0.40")
    session("s5", 1004, "walk", T(15), 100, "S3", "0.10", version=2)
    session("s6", 1001, "big4", T(20), 999, "S1", "5.00", client="bot_test")
    session("s7", 1001, "walk", T(20, month=8), 100, "S1", "0.10", version=2)
    session("s8", 1002, "big4", T(1, hour=3), 50, "S1", "0.05")

    for sid, n, at in (
        ("s1", 1, T(5, 15, 0, 0)),
        ("s1", 2, T(5, 15, 1, 0)),
        ("s1", 3, T(5, 15, 3, 0)),
        ("s3", 1, T(10, 15, 0, 0)),
        ("s3", 2, T(10, 15, 0, 30)),
    ):
        insert(db, "session_topic_events", session_id=sid, topic_number=n, reached_at=at)

    for sid, uid, rnd, q, given, ok, heard, at in (
        ("s1", 1001, 1, 1, "A", 1, "A", T(5, 15, 6)),
        ("s1", 1001, 1, 2, "C", 0, "see", T(5, 15, 7)),
        ("s2", 1001, 2, 2, "B", 1, "B", T(6, 15, 3)),
        ("s4", 1003, 1, 1, "B", 0, "be", T(12, 15, 7)),
        ("s4", 1003, 1, 2, "A", 0, "hey", T(12, 15, 8)),
        ("s4", 1003, 2, 1, "A", 1, "A", T(12, 15, 9)),
    ):
        insert(
            db,
            "quiz_answers",
            session_id=sid,
            uid=uid,
            training_id="big4",
            version_id=1,
            question_number=q,
            section_code="A",
            round=rnd,
            given_option=given,
            is_correct=ok,
            heard_as=heard,
            answered_at=at,
        )

    insert(
        db,
        "training_feedback",
        session_id="s1",
        uid=1001,
        training_id="big4",
        rating=9,
        comment="Great",
        created_at=T(5, 15, 9),
    )
    insert(
        db,
        "training_feedback",
        session_id="s3",
        uid=1002,
        training_id="big4",
        rating=5,
        trainee_quote="5, a bit long",
        created_at=T(10, 15, 5),
    )
    insert(
        db,
        "session_reviews",
        session_id="s1",
        uid=1001,
        training_id="big4",
        review_model="m",
        score=5,
        flagged=0,
        issue_count=0,
        issues="[]",
    )
    insert(
        db,
        "session_reviews",
        session_id="s3",
        uid=1002,
        training_id="big4",
        review_model="m",
        score=2,
        flagged=1,
        issue_count=2,
        issues='[{"type": "long_turn"}, {"type": "repetition"}]',
    )
    insert(
        db,
        "training_acknowledgments",
        session_id="s5",
        uid=1004,
        training_id="walk",
        version_id=2,
        statement="I understand.",
        trainee_quote="I understand it",
        acknowledged_at=T(15, 15, 2),
    )


def add_assignments(db: Session) -> None:
    """Wanaka-synced assignments for big4: two trainees who started, one who never did, one cancelled."""
    due = {1002: T(8), 1005: dt.datetime(2099, 1, 1)}  # 1002 is overdue; 1005 isn't due yet
    for uid, day in ((1001, 1), (1002, 1), (1005, 2)):
        insert(
            db,
            "training_assignments",
            uid=uid,
            training_id="big4",
            assigned_at=T(day, 12),
            due_at=due.get(uid),
            status="assigned",
        )
    insert(db, "training_assignments", uid=1003, training_id="big4", assigned_at=T(3, 12), status="cancelled")


REVIEW_ISSUES = (
    '[{"type": "false_praise", "time": "15:01:10", "quote": "Great answer, that is exactly",'
    ' "detail": "Praised a wrong answer"},'
    ' {"type": "long_turn", "time": "15:05:00", "quote": "never said", "detail": "Long turn"}]'
)


def add_session_details(db: Session) -> dict[str, str]:
    """Transcript, issues, review quotes and recordings for the session viewer (Phase 4).

    Recording ages are relative to today, because the API decides "expired" from the real clock: rec-new
    (yesterday, in S3), rec-old (120 days ago, deleted by the lifecycle rule) and rec-gone (yesterday, key set
    but the file never reached S3). s1 has no recording. Returns the S3 keys.
    """
    conn = db.connection()
    for seq, role, message, seconds, cut in (
        (1, "trainer", "Hi, I'm Anne. Let's start with topic one.", 0, 0),
        (2, "trainee", "Okay sounds good", 6, 0),
        (3, "trainer", "Great answer, that is exactly right!", 70, 1),
        (4, "trainee", "I think it's C", 400, 0),
    ):
        insert(
            db,
            "session_transcripts",
            session_id="s1",
            seq=seq,
            role=role,
            message=message,
            seconds_into_session=seconds,
            interrupted=cut,
            created_at=T(5) + dt.timedelta(seconds=seconds),
        )
    insert(
        db,
        "session_issues",
        session_id="s1",
        issue_type="guardrail",
        detail="Removed a guessed answer",
        occurred_at=T(5, 15, 2),
    )
    insert(
        db,
        "session_issues",
        session_id="s1",
        issue_type="dropped",
        detail="UNKNOWN_REASON",
        occurred_at=T(5, 15, 4),
    )
    conn.execute(
        text("UPDATE session_reviews SET issues = :issues, summary = 'Went well' WHERE session_id = 's1'"),
        {"issues": REVIEW_ISSUES},
    )
    conn.execute(
        text(
            "UPDATE training_sessions SET outcome = 'passed', end_reason = 'completed'"
            " WHERE session_id = 's2'"
        )
    )
    conn.execute(text("UPDATE training_sessions SET outcome = 'not_passed' WHERE session_id = 's4'"))

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    keys = {}
    for sid, age in (("rec-new", 1), ("rec-old", 120), ("rec-gone", 1)):
        started = now - dt.timedelta(days=age)
        keys[sid] = f"recordings/{started:%Y/%m}/{sid}.ogg"
        insert(
            db,
            "training_sessions",
            session_id=sid,
            uid=1004,
            training_id="walk",
            version_id=2,
            room_name=f"room-{sid}",
            started_at=started,
            duration_sec=60,
            start_point="new",
            store_id_at_session="S3",
            client="web_test",
            recording_s3_key=keys[sid],
        )
    return keys
