"""Build a throwaway local database for the dashboard's Playwright tests (nudgelabdashboard/e2e).

    .venv/Scripts/python scripts/e2e_seed.py mysql+pymysql://root:@127.0.0.1:3306/nudgeai_e2e?charset=utf8mb4

Drops and recreates the database, migrates it to head, loads the same fixed dataset the API tests use
(tests/agent_data.py) and adds three dashboard users with a known password. Refuses anything that isn't a
local database whose name ends in `_e2e`: it must never touch a real one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

E2E_PASSWORD = "e2e-password-1234"  # local throwaway database only
USERS = [
    ("e2e-admin@example.com", "E2E Admin", "admin", False),
    ("e2e-trainer@example.com", "E2E Trainer", "trainer", False),
    ("e2e-new@example.com", "E2E Newcomer", "trainer", True),  # must change password first
]


def main(url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    parsed = make_url(url)
    if parsed.host not in ("127.0.0.1", "localhost") or not (parsed.database or "").endswith("_e2e"):
        raise SystemExit(f"Refusing {parsed.host}/{parsed.database}: only a local *_e2e database.")

    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "e2e-placeholder-secret-not-used-anywhere-real")

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

    from app.auth.passwords import hash_password
    from app.models.dashboard import DashUser
    from tests.agent_data import add_assignments, add_session_details, seed

    engine = create_engine(url)
    with Session(engine) as db:
        seed(db)
        add_assignments(db)
        add_session_details(db)
        # Voices for the voice samples page: Ruth is the default; Amy is switched off.
        db.execute(
            text("UPDATE training_voices SET is_default = 0, sort_order = 60 WHERE voice_id = :v"),
            {"v": "Matthew"},
        )
        for voice, language, default, active, order in (
            ("Ruth", "en-US", 1, 1, 10),
            ("Danielle", "en-US", 0, 1, 20),
            ("Amy", "en-GB", 0, 0, 80),
        ):
            db.execute(
                text(
                    "INSERT INTO training_voices (voice_id, display_name, language_code, gender, is_default,"
                    " is_active, sort_order) VALUES (:v, :v, :l, 'Female', :d, :a, :o)"
                ),
                {"v": voice, "l": language, "d": default, "a": active, "o": order},
            )
        # Setups for the setups page: `standard` on Haiku 4.5 is the default; `deep` on Sonnet 5.5.
        db.execute(
            text(
                "UPDATE training_profiles SET llm_model = 'us.anthropic.claude-haiku-4-5-20251001-v1:0',"
                " is_default = 1 WHERE profile_id = 'standard'"
            )
        )
        db.execute(
            text(
                "INSERT INTO training_profiles (profile_id, display_name, llm_model, llm_effort,"
                " llm_input_per_m, llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m,"
                " tts_per_m_chars, stt_per_minute)"
                " VALUES ('deep', 'Deep', 'us.anthropic.claude-sonnet-5-5', 'low',"
                " 3, 0.3, 3.75, 15, 30, 0.024)"
            )
        )
        # Big 4's live version gets real content, for the preview call test (it passes the checks).
        big4 = (ROOT / "tests" / "fixtures" / "content" / "big4.json").read_text(encoding="utf-8")
        db.execute(
            text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
            {"c": big4},
        )
        # A Role Play training (docs/ROLEPLAY.md) with a few scored practices, for the reason picker and the
        # Role Play report.
        from tests.agent_data import T, insert

        wec_path = ROOT / "tests" / "fixtures" / "content" / "win_every_customer.json"
        wec = wec_path.read_text(encoding="utf-8")
        insert(db, "trainings", training_id="wec", title="Win Every Customer", status="active",
               completion_type="roleplay")  # fmt: skip
        insert(db, "training_versions", version_id=50, training_id="wec", version_label="v1",
               content_hash="c" * 64, content=wec, status="published")  # fmt: skip
        db.execute(text("UPDATE trainings SET active_version_id = 50 WHERE training_id = 'wec'"))
        for uid, track, tier, score, day in (
            (1001, "billing", "beginner", 3, 5),
            (1001, "billing", "beginner", 4, 6),
            (1002, "billing", "beginner", 2, 7),
            (1003, "conduct", "beginner", 5, 9),
        ):
            insert(db, "roleplay_attempts", uid=uid, training_id="wec", track=track, tier=tier, score=score,
                   quick_pauses=int(score < 4), persona="Confused About the Bill", started_at=T(day),
                   strength="Understood first", gap="Skipped the recap", tip="Recap the bill")  # fmt: skip
        # 1001 passed on Conduct, then again on Billing after a new reason (the employee page's passes).
        insert(db, "training_progress", uid=1001, training_id="wec", version_id=50, track="billing",
               status="passed", passed_at=T(9), topics_covered="[]", correct_questions="[]")  # fmt: skip
        insert(db, "training_pass_log", uid=1001, training_id="wec", version_id=50, track="conduct",
               kind="first", passed_at=T(4))  # fmt: skip
        insert(db, "training_pass_log", uid=1001, training_id="wec", version_id=50, track="billing",
               kind="repeat", passed_at=T(9))  # fmt: skip
        # The session viewer test needs a recorded session with a transcript: give rec-new s1's lines.
        db.execute(
            text(
                "INSERT INTO session_transcripts"
                " (session_id, seq, role, message, seconds_into_session, interrupted, created_at)"
                " SELECT 'rec-new', seq, role, message, seconds_into_session, interrupted, created_at"
                " FROM session_transcripts WHERE session_id = 's1'"
            )
        )
        for email, name, role, must_change in USERS:
            db.add(
                DashUser(
                    email=email,
                    full_name=name,
                    password_hash=hash_password(E2E_PASSWORD),
                    role=role,
                    is_active=True,
                    must_change_password=must_change,
                    timezone="America/Chicago",
                    preferences={},
                )
            )
        db.commit()
    engine.dispose()
    print(f"e2e database ready: {parsed.database}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
