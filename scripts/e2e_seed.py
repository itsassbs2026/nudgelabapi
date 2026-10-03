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
