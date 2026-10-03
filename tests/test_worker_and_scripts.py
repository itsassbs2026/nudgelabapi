"""Email outbox, token cleanup and the first-Admin script."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings, get_settings
from app.models.dashboard import DashEmailOutbox, DashRefreshToken, DashUser
from app.notifications.graph import GraphMailError
from app.services import email_sender, token_cleanup
from sqlalchemy import select
from sqlalchemy.orm import Session


def _graph_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "email_enabled": True,
            "graph_tenant_id": "t",
            "graph_client_id": "c",
            "graph_client_secret": "s",
            "graph_sender_mailbox": "sender@example.com",
        }
    )


def _queue(db: Session, to: str = "a@example.com") -> DashEmailOutbox:
    row = DashEmailOutbox(
        to_email=to,
        subject="S",
        body_html="<p>x</p>",
        template="password_reset",
        status="pending",
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(row)
    db.flush()
    return row


def test_outbox_waits_while_graph_is_not_configured(db_session: Session) -> None:
    row = _queue(db_session)
    assert email_sender.drain_email_outbox(db_session, get_settings()) == 0
    assert row.status == "pending"


def test_outbox_sends_through_graph(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(email_sender, "send_email", lambda s, to, subject, body: sent.append(to))
    row = _queue(db_session, "b@example.com")
    assert email_sender.drain_email_outbox(db_session, _graph_settings()) == 1
    assert sent == ["b@example.com"] and row.status == "sent" and row.sent_at is not None


def test_outbox_retries_then_gives_up(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object) -> None:
        raise GraphMailError("boom")

    monkeypatch.setattr(email_sender, "send_email", fail)
    row = _queue(db_session)
    for expected in ("failed", "failed", "failed", "dead"):
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()
        email_sender.drain_email_outbox(db_session, _graph_settings())
        assert row.status == expected
    assert row.attempts == 4 and row.last_error == "boom"


def test_token_cleanup_keeps_recent_tokens(db_session: Session, make_user: Callable[..., DashUser]) -> None:
    user = make_user()
    now = datetime.now(UTC)
    for days, fam in ((-30, "old"), (-1, "recent")):
        db_session.add(
            DashRefreshToken(
                user_id=user.id,
                token_hash=fam.ljust(64, "0"),
                family_id=fam,
                expires_at=now + timedelta(days=days),
            )
        )
    db_session.flush()
    assert token_cleanup.purge_expired_tokens(db_session, get_settings()) == 1
    assert [r.family_id for r in db_session.execute(select(DashRefreshToken)).scalars()] == ["recent"]


def test_bootstrap_admin_creates_once(db_session: Session) -> None:
    from scripts.bootstrap_admin import bootstrap

    settings = get_settings().model_copy(
        update={
            "bootstrap_admin_email": "BGupta@primecomms.com",
            "bootstrap_admin_password": "a-strong-first-password",
        }
    )
    admin = bootstrap(db_session, settings)
    assert admin.email == "bgupta@primecomms.com" and admin.role == "admin" and admin.must_change_password
    with pytest.raises(SystemExit, match="already exists"):
        bootstrap(db_session, settings)


def test_bootstrap_admin_rejects_a_weak_password(db_session: Session) -> None:
    from scripts.bootstrap_admin import bootstrap

    settings = get_settings().model_copy(
        update={"bootstrap_admin_email": "a@example.com", "bootstrap_admin_password": "short"}
    )
    with pytest.raises(SystemExit, match="BOOTSTRAP_ADMIN_PASSWORD"):
        bootstrap(db_session, settings)
