# ruff: noqa: E501 - long SQL statements read better unwrapped
"""Build a local database at full-rollout size for the load test (scripts/load_test.py, SPEC Phase 17).

    .venv/Scripts/python scripts/load_seed.py mysql+pymysql://root:@127.0.0.1:3306/nudgeai_load?charset=utf8mb4

Everyone trains: 50,500 trainees in 2,542 stores (production's real counts), 10 trainings, six assigned to
each person, and 300,000 sessions over the last 150 days, with their progress, usage, quiz answers, topic events,
feedback and reviews. Bulk SQL, a few minutes. Plus the dashboard Admin `load-admin@example.com` (no password
anyone knows: the load test signs its own token). Refuses anything that isn't a local database ending `_load`.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STORES, USERS, SESSIONS, DAYS = 2_542, 50_500, 300_000, 150
TRAININGS = [(f"t{i:02d}", f"Training {i}", "quiz" if i <= 6 else "walkthrough" if i <= 9 else "acknowledgment")
             for i in range(1, 11)]  # fmt: skip
TOPICS, QUESTIONS = 12, 5


def main(url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.host not in ("127.0.0.1", "localhost") or not (parsed.database or "").endswith("_load"):
        raise SystemExit(f"Refusing {parsed.host}/{parsed.database}: only a local *_load database.")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "load-test-placeholder-secret-not-used-anywhere-real")

    server = create_engine(parsed.set(database=""))
    with server.connect() as conn:
        is_mariadb = "mariadb" in str(conn.execute(text("SELECT VERSION()")).scalar()).lower()
        collation = "utf8mb4_unicode_ci" if is_mariadb else "utf8mb4_0900_ai_ci"
        conn.execute(text(f"DROP DATABASE IF EXISTS `{parsed.database}`"))
        conn.execute(text(f"CREATE DATABASE `{parsed.database}` CHARACTER SET utf8mb4 COLLATE {collation}"))
    server.dispose()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    steps: list[tuple[str, str]] = [
        ("numbers", """CREATE TABLE seq (n INT UNSIGNED PRIMARY KEY)"""),
        (
            "numbers",
            """INSERT INTO seq SELECT a.d + b.d * 10 + c.d * 100 + e.d * 1000 + f.d * 10000 + g.d * 100000
            FROM (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a
            CROSS JOIN (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b
            CROSS JOIN (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) c
            CROSS JOIN (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) e
            CROSS JOIN (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) f
            CROSS JOIN (SELECT 0 d UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5
                  UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) g""",
        ),
        (
            "stores",
            f"""INSERT INTO v_stores_all (store_id, store_name, store_active, region_id, region_name, market_id,
                market_name, district_id, district_name)
            SELECT CONCAT('S', n), CONCAT('Store ', n), 1, n % 6 + 1, CONCAT('Region ', n % 6 + 1), n % 40 + 1,
                CONCAT('Market ', n % 40 + 1), n % 300 + 1, CONCAT('District ', n % 300 + 1)
            FROM seq WHERE n < {STORES}""",
        ),
        (
            "trainees",
            f"""INSERT INTO v_users_all (uid, name, status, store_id, job_title)
            SELECT 100000 + n, CONCAT('Trainee ', n), IF(n % 50 = 0, 0, 1), CONCAT('S', n % {STORES}),
                IF(n % 9 = 0, 'Store Manager', 'RSC')
            FROM seq WHERE n < {USERS}""",
        ),
        (
            "profiles",
            """INSERT INTO training_profiles (profile_id, display_name, llm_model, llm_input_per_m,
                llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m, tts_per_m_chars, stt_per_minute,
                is_default)
            VALUES ('standard', 'Standard', 'us.anthropic.claude-haiku-4-5-20251001-v1:0', 1, 0.1, 1.25, 5, 30,
                0.024, 1)""",
        ),
        (
            "voices",
            """INSERT INTO training_voices (voice_id, display_name, language_code, gender, is_default)
            VALUES ('Matthew', 'Matthew', 'en-US', 'Male', 1)""",
        ),
    ]
    for i, (tid, title, ctype) in enumerate(TRAININGS, start=1):
        steps += [
            (
                "trainings",
                f"""INSERT INTO trainings (training_id, title, status, completion_type)
                VALUES ('{tid}', '{title}', 'active', '{ctype}')""",
            ),
            (
                "trainings",
                f"""INSERT INTO training_versions (version_id, training_id, version_label, content_hash,
                    status) VALUES ({i}, '{tid}', 'v1', LPAD('{i}', 64, '0'), 'published')""",
            ),
            ("trainings", f"UPDATE trainings SET active_version_id = {i} WHERE training_id = '{tid}'"),
            (
                "trainings",
                f"""INSERT INTO training_topics (version_id, topic_number, title)
                SELECT {i}, n, CONCAT('Topic ', n) FROM seq WHERE n BETWEEN 1 AND {TOPICS}""",
            ),
        ]
        if ctype == "quiz":
            steps.append(
                (
                    "trainings",
                    f"""INSERT INTO training_questions (version_id, question_number,
                    location_variant, section_code, section_name, question_text, options, correct_option)
                SELECT {i}, n, '', 'A', 'Section A', CONCAT('Question ', n), '{{"A": "a", "B": "b", "C": "c"}}',
                    ELT(n % 3 + 1, 'A', 'B', 'C')
                FROM seq WHERE n BETWEEN 1 AND {QUESTIONS}""",
                )
            )
    tcase = " ".join(f"WHEN {i - 1} THEN '{tid}'" for i, (tid, _, _) in enumerate(TRAININGS, start=1))
    quiz_ids = ", ".join(f"'{tid}'" for tid, _, c in TRAININGS if c == "quiz")
    steps += [
        ("assignments", f"""INSERT INTO training_assignments (uid, training_id, assigned_at, due_at, status)
            SELECT 100000 + s.n, CASE t.n {tcase} END, UTC_TIMESTAMP() - INTERVAL 160 DAY,
                UTC_TIMESTAMP() - INTERVAL (s.n % 120) DAY, IF(s.n % 97 = 0, 'cancelled', 'assigned')
            FROM seq s JOIN seq t ON t.n < 10
            WHERE s.n < {USERS} AND (s.n + t.n) % 10 < 6"""),
        ("sessions", f"""INSERT INTO training_sessions (session_id, uid, training_id, version_id, room_name,
                started_at, ended_at, duration_sec, start_point, outcome, end_reason, store_id_at_session, client,
                agent_version, llm_model, voice_id, profile_id)
            SELECT CONCAT('L', n), 100000 + (n * 7919) % {USERS}, CASE n % 10 {tcase} END, n % 10 + 1,
                CONCAT('nl-load-', n),
                UTC_TIMESTAMP() - INTERVAL (n % ({DAYS} * 1440)) MINUTE,
                UTC_TIMESTAMP() - INTERVAL (n % ({DAYS} * 1440)) MINUTE + INTERVAL (120 + n % 900) SECOND,
                120 + n % 900, IF(n % 4 = 0, 'resume', 'new'),
                CASE WHEN n % 10 >= 6 THEN 'no_quiz' WHEN n % 3 = 0 THEN 'not_passed' ELSE 'passed' END,
                ELT(n % 20 + 1, 'completed', 'completed', 'completed', 'completed', 'completed', 'completed',
                    'completed', 'completed', 'completed', 'completed', 'completed', 'completed', 'user_left',
                    'user_left', 'user_left', 'user_left', 'dropped', 'error', 'completed', 'completed'),
                CONCAT('S', ((n * 7919) % {USERS}) % {STORES}), IF(n % 25 = 0, 'web_test', 'flutter'),
                'load', 'claude-haiku-4-5', 'Matthew', 'standard'
            FROM seq WHERE n < {SESSIONS}"""),
        ("usage", """INSERT INTO session_usage (session_id, llm_model, llm_requests, llm_input_tokens,
                llm_cached_tokens, llm_cache_write_tokens, llm_output_tokens, tts_characters, stt_audio_seconds,
                est_llm_cost, est_tts_cost, est_stt_cost, est_total_cost)
            SELECT session_id, 'claude-haiku-4-5', 20, 90000, 85000, 4000, 900, 9000, duration_sec * 0.4,
                0.03, 0.27, 0.01, 0.31
            FROM training_sessions"""),
        ("quiz answers", f"""INSERT INTO quiz_answers (session_id, uid, training_id, version_id, question_number,
                section_code, round, given_option, is_correct, heard_as, answered_at)
            SELECT s.session_id, s.uid, s.training_id, s.version_id, q.n, 'A', 1,
                ELT((q.n + CRC32(s.session_id)) % 3 + 1, 'A', 'B', 'C'),
                IF(s.outcome = 'passed' OR (CRC32(s.session_id) + q.n) % 3 = 0, 1, 0), 'A',
                s.started_at + INTERVAL (60 + q.n * 20) SECOND
            FROM training_sessions s JOIN seq q ON q.n BETWEEN 1 AND {QUESTIONS}
            WHERE s.training_id IN ({quiz_ids}) AND s.outcome IN ('passed', 'not_passed')"""),
        ("topic events", f"""INSERT INTO session_topic_events (session_id, topic_number, reached_at)
            SELECT s.session_id, t.n, s.started_at + INTERVAL (t.n * 50) SECOND
            FROM training_sessions s JOIN seq t ON t.n BETWEEN 1 AND {TOPICS}
            WHERE t.n * 75 <= s.duration_sec"""),
        ("progress", f"""INSERT INTO training_progress (uid, training_id, version_id, status, topics_covered,
                walkthrough_finished_at, quiz_attempted, correct_questions, sessions_count, first_started_at,
                passed_at)
            SELECT uid, training_id, MIN(version_id),
                IF(SUM(outcome = 'passed' OR (outcome = 'no_quiz' AND end_reason = 'completed')) > 0, 'passed',
                   'in_progress'),
                JSON_ARRAY(1, 2, 3), MIN(ended_at), MAX(training_id IN ({quiz_ids})), JSON_ARRAY(1, 2),
                COUNT(*), MIN(started_at),
                IF(SUM(outcome = 'passed' OR (outcome = 'no_quiz' AND end_reason = 'completed')) > 0,
                   MAX(ended_at), NULL)
            FROM training_sessions GROUP BY uid, training_id"""),
        ("feedback", """INSERT INTO training_feedback (session_id, uid, training_id, rating, trainee_quote, created_at)
            SELECT session_id, uid, training_id, 3 + CRC32(session_id) % 8, 'It was fine.', ended_at
            FROM training_sessions WHERE end_reason = 'completed' AND CRC32(session_id) % 10 < 3"""),
        ("reviews", """INSERT INTO session_reviews (session_id, uid, training_id, session_started, review_model,
                score, flagged, issue_count, issues, summary)
            SELECT session_id, uid, training_id, started_at, 'load', 1 + CRC32(session_id) % 5,
                IF(CRC32(session_id) % 5 = 0, 1, 0), IF(CRC32(session_id) % 5 = 0, 1, 0),
                IF(CRC32(session_id) % 5 = 0, '[{"type": "long_turn"}]', '[]'), 'Fine.'
            FROM training_sessions WHERE CRC32(session_id) % 4 = 0"""),
        ("numbers", "DROP TABLE seq"),
        ("statistics", "ANALYZE TABLE training_sessions, session_usage, quiz_answers, session_topic_events, "
                       "training_progress, training_assignments, training_feedback, session_reviews, v_users_all, "
                       "v_stores_all"),
    ]  # fmt: skip

    with engine.begin() as conn:
        for label, sql in steps:
            started = time.monotonic()
            result = conn.execute(text(sql))
            rows = result.rowcount if result.rowcount and result.rowcount > 0 else ""
            print(f"{label:14} {rows!s:>9}  {time.monotonic() - started:5.1f} s", flush=True)

    from app.auth.passwords import hash_password
    from app.models.dashboard import DashUser
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        db.add(DashUser(email="load-admin@example.com", full_name="Load Admin", role="admin", is_active=True,
                        password_hash=hash_password(os.urandom(24).hex()), must_change_password=False,
                        timezone="America/Chicago", preferences={}))  # fmt: skip
        db.commit()
    engine.dispose()
    print(f"load database ready: {parsed.database}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
