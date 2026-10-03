"""The migrated test database must match production's structure (baseline) plus the API tables, and the
migration safety rails must hold."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.models import API_TABLES, Base, DashUser
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config, assert_safe_test_url

AGENT_TABLES = {
    "trainings",
    "training_versions",
    "training_topics",
    "training_questions",
    "training_progress",
    "training_sessions",
    "session_transcripts",
    "session_topic_events",
    "session_usage",
    "session_issues",
    "session_reviews",
    "daily_review_reports",
    "quiz_answers",
    "training_feedback",
    "training_acknowledgments",
    "training_assignments",
    "completion_writes",
    "training_voices",
    "training_profiles",
    "store_location_types",
}
EXTERNAL_TABLES = {"v_users_all", "v_stores_all", "v_users", "v_stores"}
VIEWS = {
    "vw_trainees",
    "vw_training_stores",
    "vw_session_report",
    "vw_question_stats",
    "vw_assignment_status",
}


def test_every_table_and_view_exists(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert tables >= AGENT_TABLES
    assert tables >= EXTERNAL_TABLES
    assert tables >= API_TABLES
    assert set(insp.get_view_names()) >= VIEWS


def test_views_compile(engine: Engine) -> None:
    with engine.connect() as conn:
        for view in VIEWS:
            conn.execute(text(f"SELECT * FROM {view} LIMIT 1"))


def test_models_match_the_database(engine: Engine) -> None:
    """Same check as `alembic check`: no API table drifts from its model."""
    with engine.connect() as conn:
        diffs = compare_metadata(
            MigrationContext.configure(
                conn, opts={"include_object": lambda o, n, t, r, c: t != "table" or n in API_TABLES}
            ),
            Base.metadata,
        )
    assert diffs == []


def test_baseline_refuses_an_existing_schema(engine: Engine) -> None:
    cfg = alembic_config()
    command.stamp(cfg, "base")
    try:
        with pytest.raises(RuntimeError, match="must be STAMPED"):
            command.upgrade(cfg, "0001_baseline")
    finally:
        command.stamp(cfg, "head")


def test_role_check_constraint(db_session: Session) -> None:
    db_session.add(DashUser(email="x@example.com", full_name="X", password_hash="h", role="superuser"))
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.flush()


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://u:p@primetwok8-testing.example.us-west-1.rds.amazonaws.com:3306/nudgeai_test",
        "mysql+pymysql://u:p@127.0.0.1:3306/nudgeai",
        "mysql+pymysql://u:p@10.0.0.5:3306/nudgeai_test",
    ],
)
def test_test_guard_refuses_non_local_or_non_test_databases(url: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing"):
        assert_safe_test_url(url)


def test_test_guard_accepts_local_test_database() -> None:
    assert_safe_test_url("mysql+pymysql://root:@127.0.0.1:3306/nudgeai_test")
