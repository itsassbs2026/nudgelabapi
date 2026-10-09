"""The daily summary email (app/summary): its figures, its words, sending once a day, and its recipients."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from app.config import get_settings
from app.models.dashboard import DashEmailOutbox
from app.models.summary import DashSummaryRecipient, DashSummaryRun
from app.summary import narrative, send
from app.summary.data import daily_summary
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.agent_data import seed

TZ = "America/Chicago"
WORDS = {"headline": "One person passed.", "went_well": ["Big 4 did well."], "needs_attention": ["Watch it."]}


@pytest.fixture
def data(db_session: Session) -> None:
    seed(db_session)


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Claude replaced by a fake that records what it was given."""
    seen: list[dict[str, Any]] = []

    def fake(_settings: Any, figures: dict[str, Any]) -> dict[str, Any]:
        seen.append(figures)
        return WORDS

    monkeypatch.setattr(narrative, "writer", fake)
    return seen


def only(db: Session, *emails: str) -> None:
    db.execute(delete(DashSummaryRecipient))
    for e in emails:
        db.add(DashSummaryRecipient(email=e, is_active=True))
    db.flush()


def test_the_figures_match_the_reports(db_session: Session, data: None) -> None:
    s = daily_summary(db_session, date(2026, 9, 6), TZ)
    k = s["kpis"]
    assert (k["calls"], k["people"], k["passed"], k["pass_rate"]) == (1, 1, 1, 1.0)
    assert s["day_before"]["calls"] == 1 and s["day_before"]["passed"] == 0
    assert s["week_average"]["calls"] == round(2 / 7, 1)  # s8 (Aug 31 Central) and s1 (Sep 5)
    (big4,) = s["trainings"]
    assert big4["title"] and big4["passed"] == 1 and big4["avg_minutes"] == 5.0
    assert set(s["attention"]) == {
        "never_connected", "early_leaves", "stuck_people", "stuck_calls", "low_ratings", "overdue",
    }  # fmt: skip
    assert s["roleplay"] is None
    bots_only = daily_summary(db_session, date(2026, 9, 20), TZ)  # only a bot call that day
    assert bots_only["kpis"]["calls"] == 0 and bots_only["trainings"] == []


def test_the_words_fall_back_to_rules(
    db_session: Session, data: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    figures = daily_summary(db_session, date(2026, 9, 6), TZ)
    monkeypatch.setattr(
        narrative, "writer", lambda *_: {"headline": "Hi", "went_well": [], "needs_attention": []}
    )
    words, by_claude = narrative.write(get_settings(), figures)
    assert not by_claude and "1 person passed" in words["headline"]  # incomplete answer: rules instead

    def broken(*_: Any) -> dict[str, Any]:
        raise RuntimeError("Bedrock is down")

    monkeypatch.setattr(narrative, "writer", broken)
    assert narrative.write(get_settings(), figures)[1] is False
    empty = daily_summary(db_session, date(2026, 9, 20), TZ)
    assert narrative.write(get_settings(), empty)[0]["headline"] == "No training calls yesterday."


def test_a_day_is_sent_once_to_active_recipients(
    db_session: Session, data: None, claude: list[dict[str, Any]]
) -> None:
    only(db_session, "a@example.com", "b@example.com")
    paused = DashSummaryRecipient(email="c@example.com", is_active=False)
    db_session.add(paused)
    db_session.flush()
    day = date(2026, 9, 6)
    assert send.send_daily(db_session, get_settings(), day) == 2
    mails = (
        db_session.execute(select(DashEmailOutbox).where(DashEmailOutbox.template == "daily_summary"))
        .scalars()
        .all()
    )
    assert sorted(m.to_email for m in mails) == ["a@example.com", "b@example.com"]
    assert mails[0].subject == "NudgeLab daily summary: Sun, Sep 6"
    html = mails[0].body_html
    assert "One person passed." in html and "Big 4 did well." in html and "Open the dashboard" in html
    assert "from=2026-09-06&amp;to=2026-09-06" in html and "written by rules" not in html
    assert claude[0]["kpis"]["passed"] == 1  # Claude saw only the figures
    assert send.send_daily(db_session, get_settings(), day) is None  # already sent
    run = db_session.get(DashSummaryRun, day)
    assert run is not None and run.recipients == 2 and run.by_claude


def test_yesterday_is_the_central_day() -> None:
    settings = get_settings()
    assert send.yesterday(settings, datetime(2026, 10, 9, 12, 0, tzinfo=UTC)) == date(2026, 10, 8)
    assert send.yesterday(settings, datetime(2026, 10, 9, 3, 0, tzinfo=UTC)) == date(
        2026, 10, 7
    )  # 10 PM on the 8th


def test_admins_manage_the_recipients(
    client: TestClient, admin_headers: dict[str, str], trainer_headers: dict[str, str], db_session: Session
) -> None:
    only(db_session, "bgupta@primecomms.com")
    url = "/api/v1/admin/summary/recipients"
    assert [r["email"] for r in client.get(url, headers=admin_headers).json()] == ["bgupta@primecomms.com"]
    added = client.post(url, headers=admin_headers, json={"email": "  Kseniia@PrimeComms.com "})
    assert added.status_code == 201 and added.json()["email"] == "kseniia@primecomms.com"
    again = client.post(url, headers=admin_headers, json={"email": "kseniia@primecomms.com"})
    assert again.status_code == 409
    assert client.post(url, headers=admin_headers, json={"email": "not an email"}).status_code == 422
    rid = added.json()["id"]
    paused = client.patch(f"{url}/{rid}", headers=admin_headers, json={"is_active": False})
    assert paused.status_code == 200 and paused.json()["is_active"] is False
    assert client.delete(f"{url}/{rid}", headers=admin_headers).status_code == 204
    assert client.delete(f"{url}/{rid}", headers=admin_headers).status_code == 404
    assert client.get(url, headers=trainer_headers).status_code == 403


def test_a_test_send_goes_to_the_admin_only(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session: Session,
    data: None,
    claude: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only(db_session, "someone@example.com")
    monkeypatch.setattr(send, "yesterday", lambda _settings: date(2026, 9, 6))  # the test data has calls then
    r = client.post("/api/v1/admin/summary/test", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"to": "admin@example.com", "day": "2026-09-06", "by_claude": True}
    mails = (
        db_session.execute(select(DashEmailOutbox).where(DashEmailOutbox.template == "daily_summary"))
        .scalars()
        .all()
    )
    assert [m.to_email for m in mails] == ["admin@example.com"] and mails[0].subject.startswith("[Test] ")
    assert db_session.execute(select(DashSummaryRun)).first() is None  # a test isn't the day's send
