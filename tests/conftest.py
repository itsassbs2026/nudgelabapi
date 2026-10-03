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
# Test-only signing secret and settings; never used outside tests.
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["EMAIL_ENABLED"] = "false"
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/unused_test")
for _key in (
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "GRAPH_TENANT_ID",
    "GRAPH_CLIENT_ID",
    "GRAPH_CLIENT_SECRET",
    "GRAPH_SENDER_MAILBOX",
):
    os.environ.pop(_key, None)

from collections.abc import Callable, Generator  # noqa: E402
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
            # Same default collation as the agent's tables (0001_baseline), as on production (0900_ai_ci on
            # MySQL 8): the API's tables take the default, and joins between the two need matching collations.
            is_mariadb = "mariadb" in str(conn.execute(text("SELECT VERSION()")).scalar()).lower()
            collation = "utf8mb4_unicode_ci" if is_mariadb else "utf8mb4_0900_ai_ci"
            conn.execute(text(f"CREATE DATABASE `{url.database}` CHARACTER SET utf8mb4 COLLATE {collation}"))
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


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> None:
    """The app's limiter is a module-level singleton and TestClient always has the same address."""
    from app.auth.rate_limit import limiter

    limiter.reset()


GOOD_PASSWORD = "correct-horse-battery-1"


@pytest.fixture
def make_user(db_session: Session) -> Callable[..., object]:
    from app.auth.passwords import hash_password
    from app.models.dashboard import DashUser

    counter = {"n": 0}

    def _make(
        *,
        role: str = "trainer",
        email: str | None = None,
        password: str = GOOD_PASSWORD,
        is_active: bool = True,
        must_change_password: bool = False,
    ) -> DashUser:
        counter["n"] += 1
        user = DashUser(
            email=email or f"user{counter['n']}@example.com",
            full_name=f"User {counter['n']}",
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
            must_change_password=must_change_password,
            timezone="America/Chicago",
            preferences={},
        )
        db_session.add(user)
        db_session.flush()
        return user

    return _make


@pytest.fixture
def login(client: TestClient) -> Callable[..., dict[str, str]]:
    """Signs in and returns the Authorization header."""

    def _login(email: str, password: str = GOOD_PASSWORD) -> dict[str, str]:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def admin_headers(make_user: Callable[..., object], login: Callable[..., dict[str, str]]) -> dict[str, str]:
    admin = make_user(role="admin", email="admin@example.com")
    return login(admin.email)  # type: ignore[attr-defined]


@pytest.fixture
def trainer_headers(make_user: Callable[..., object], login: Callable[..., dict[str, str]]) -> dict[str, str]:
    trainer = make_user(role="trainer", email="trainer@example.com")
    return login(trainer.email)  # type: ignore[attr-defined]
