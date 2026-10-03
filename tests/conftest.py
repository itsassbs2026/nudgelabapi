"""Tests run against a disposable local database only (SPEC §0.4, CLAUDE.md).

TEST_DATABASE_URL must point at a database whose name ends in "_test" on a local server (127.0.0.1, localhost,
or the "mysql" service in CI). At the start of a test run that database is dropped, recreated and migrated to
head with Alembic, so tests always see the same schema as production (baseline + API tables). Anything else is
refused before a single statement runs: the production nudgeai database can never be touched by tests.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

SAFE_TEST_HOSTS = {"127.0.0.1", "localhost", "mysql"}


def assert_safe_test_url(url: str) -> None:
    parsed = make_url(url)
    if parsed.host not in SAFE_TEST_HOSTS:
        raise RuntimeError(
            f"Refusing to run tests against host {parsed.host!r}: tests only use a local database."
        )
    if not (parsed.database or "").endswith("_test"):
        raise RuntimeError(
            f"Refusing to run tests against database {parsed.database!r}: its name must end in _test."
        )


_TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
if _TEST_URL:
    assert_safe_test_url(_TEST_URL)
    # Must happen before any app module is imported (Settings reads the environment at import time).
    os.environ["DATABASE_URL"] = _TEST_URL
    os.environ.pop("DATABASE_MIGRATION_URL", None)

from collections.abc import Generator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    if not _TEST_URL:
        pytest.skip("TEST_DATABASE_URL is not set: no local test database.")
    url = make_url(_TEST_URL)
    server = create_engine(url.set(database=""), future=True)  # set(database=None) keeps the old name
    try:
        with server.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{url.database}`"))
            conn.execute(text(f"CREATE DATABASE `{url.database}` CHARACTER SET utf8mb4"))
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Test database server is not reachable: {exc}")
    finally:
        server.dispose()

    command.upgrade(alembic_config(), "head")
    test_engine = create_engine(_TEST_URL, future=True)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """One outer transaction per test, rolled back at the end; app commits become savepoints."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    from app.db import get_db
    from app.main import app

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
