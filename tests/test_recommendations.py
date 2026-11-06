"""Content recommendations (app/recommendations, 2026-11-05): evidence, the nightly run, our email, Studio."""

from __future__ import annotations

import datetime as dt
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from app.config import get_settings
from app.models.dashboard import DashEmailOutbox
from app.models.recommendations import DashRecommendation, DashRecommendationRun
from app.recommendations import evidence, run, writer
from app.reports.filters import build_filters
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests.agent_data import insert, seed

RUN = date(2026, 9, 30)  # looks at Sep 23-29, Central
TZ = "America/Chicago"


def at(day: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, day, 17, minute)  # noon Central


@pytest.fixture
def week(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The Big 4 in the week before RUN: 22 calls; question 1 missed by most, nearly always as C."""
    seed(db_session)
    monkeypatch.setattr(
        get_settings(), "recommendations_min_calls", 15
    )  # 18 calls in the next night's window
    monkeypatch.setattr(get_settings(), "recommendations_not_before", None)
    for i in range(22):
        sid = f"r{i:02d}"
        uid = 1001 + i % 5
        passed = i % 3 == 0
        insert(db_session, "training_sessions", session_id=sid, uid=uid, training_id="big4", version_id=1,
               room_name=f"room-{sid}", started_at=at(23 + i % 7, i), duration_sec=420, start_point="new",
               client="flutter", outcome="passed" if passed else "not_passed",
               end_reason="completed" if passed else "user_left")  # fmt: skip
        insert(
            db_session, "session_topic_events", session_id=sid, topic_number=1, reached_at=at(23 + i % 7, i)
        )
        if not passed and i % 2:
            insert(db_session, "session_topic_events", session_id=sid, topic_number=2,
                   reached_at=at(23 + i % 7, i + 1))  # fmt: skip
        right = i % 4 == 0
        insert(db_session, "quiz_answers", session_id=sid, uid=uid, training_id="big4", version_id=1,
               question_number=1, round=1, given_option="A" if right else "C", is_correct=right,
               heard_as=None if right else "the second Tuesday",
               answered_at=at(23 + i % 7, i + 2))  # fmt: skip
        insert(db_session, "training_feedback", session_id=sid, uid=uid, training_id="big4",
               rating=3 if i % 2 else 9, trainee_quote="Way too long." if i % 2 else "Clear, thanks.",
               created_at=at(23 + i % 7, i + 3))  # fmt: skip
        for seq, role, message in (
            (1, "trainer", "Welcome to the Big 4."),
            (2, "trainee", "What does attach rate mean exactly?"),
            (3, "trainee", "Can you repeat that please"),
        ):
            insert(db_session, "session_transcripts", session_id=sid, seq=seq, role=role, message=message,
                   seconds_into_session=seq * 10, interrupted=0, created_at=at(23 + i % 7, i))  # fmt: skip
    db_session.connection().execute(
        text(
            """UPDATE training_versions SET content = '{"topics": [{"title": "Attach rate"}]}' WHERE
            version_id = 1"""
        )
    )
    db_session.flush()
    return db_session


def _f() -> Any:
    return build_filters(
        timezone=TZ, date_from=date(2026, 9, 23), date_to=date(2026, 9, 29), training_id="big4"
    )


REC = {
    "area": "options",
    "target": "Question 1",
    "impact": "high",
    "title": "Make question 1's options easier to tell apart",
    "problem": "Most trainees pick C.",
    "suggestion": "Reword C.",
    "evidence": "16 of 22 missed it, 16 chose C.",
    "example_session_ids": ["r01", "made-up-id"],
}


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    seen: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    def fake(
        _s: Any, ev: dict[str, Any], earlier: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        seen.append((ev, earlier))
        ours = [{"issue": "Answers heard wrong", "evidence": "3 calls", "example_session_ids": ["r03"]}]
        return {"recommendations": [REC, {"title": "incomplete"}], "ours": ours}, {
            "input_tokens": 9,
            "output_tokens": 3,
        }

    monkeypatch.setattr(writer, "writer", fake)
    return seen


def test_the_evidence(week: Session) -> None:
    ev = evidence.gather(week, _f())
    assert (ev["calls"], ev["people"], ev["title"]) == (22, 5, "The Big 4")
    q1 = next(q for q in ev["questions_most_missed_first"] if q["question_number"] == 1)
    assert q1["most_common_wrong_option"] == "C" and len(q1["wrong_answers_heard"]) == 8
    assert q1["wrong_answers_heard"][0]["said"] == "the second Tuesday"
    assert ev["ratings"]["count"] == 22 and ev["ratings"]["1_to_4"] == 11
    assert {q["said"] for q in ev["rating_quotes"]} == {"Way too long.", "Clear, thanks."}
    assert {h["where"] for h in ev["hangups_before_passing"]} <= {
        "during the quiz", "topic 1: Big 4 topic 1", "topic 2: Big 4 topic 2",
    }  # fmt: skip
    sample = ev["transcript_sample"]
    assert sample["confused_or_repeat_requests"] == 22  # "Can you repeat that please" in each call
    assert sample["questions_trainees_asked"][0]["said"] == "What does attach rate mean exactly?"
    assert "Attach rate" in ev["content"]
    # Only sessions from the evidence may be cited.
    assert "r01" in writer.session_ids(ev) and "made-up-id" not in writer.session_ids(ev)


def test_the_nightly_run_stores_checked_recommendations_and_emails_our_list_once(
    week: Session, claude: list[Any]
) -> None:
    settings = get_settings()
    summary = run.run_all(week, settings, RUN)
    assert summary["looked_at"] == 1 and summary["emailed"] is True  # big4; walk had no calls
    (rec,) = week.execute(select(DashRecommendation)).scalars()  # the incomplete one was dropped
    assert (rec.training_id, rec.status, rec.examples) == ("big4", "open", ["r01"])  # the made-up id too
    (r,) = week.execute(select(DashRecommendationRun)).scalars()
    assert (r.calls, r.input_tokens, r.ours[0]["examples"]) == (22, 9, ["r03"])
    (mail,) = week.execute(select(DashEmailOutbox).where(DashEmailOutbox.template == run.TEMPLATE)).scalars()
    assert mail.to_email == settings.recommendations_email
    assert "Answers heard wrong" in mail.body_html and "/sessions/r03" in mail.body_html
    # A rerun the same night does nothing.
    again = run.run_all(week, settings, RUN)
    assert (again["looked_at"], again["emailed"]) == (0, False) and len(claude) == 1


def test_the_next_night_replaces_open_ones_and_remembers_dismissed(week: Session, claude: list[Any]) -> None:
    settings = get_settings()
    run.run_all(week, settings, RUN)
    first = week.execute(select(DashRecommendation)).scalar_one()
    first.status, first.status_note = "dismissed", "We like the wording"
    week.add(DashRecommendation(run_id=first.run_id, training_id="big4", area="topic", impact="low",
                                title="Old", problem="p", suggestion="s", evidence="e",
                                status="open"))  # fmt: skip
    week.commit()
    run.run_all(week, settings, date(2026, 10, 1))
    statuses = sorted(x.status for x in week.execute(select(DashRecommendation)).scalars())
    assert statuses == ["dismissed", "open", "replaced"]
    _, earlier = claude[-1]
    assert earlier == [{"title": first.title, "target": "Question 1", "status": "dismissed",
                        "trainer_note": "We like the wording"}]  # fmt: skip


def test_a_failed_review_keeps_the_earlier_recommendations(
    week: Session, claude: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    run.run_all(week, settings, RUN)

    def broken(*_: Any) -> Any:
        raise TimeoutError("Bedrock")

    monkeypatch.setattr(writer, "writer", broken)
    run.run_all(week, settings, date(2026, 10, 1))
    assert week.execute(select(DashRecommendation.status)).scalars().all() == ["open"]
    failed = week.execute(
        select(DashRecommendationRun).where(DashRecommendationRun.error.is_not(None))
    ).scalar_one()
    assert failed.error.startswith("TimeoutError")


def test_too_few_calls_means_no_review(
    week: Session, claude: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "recommendations_min_calls", 23)
    summary = run.run_all(week, get_settings(), RUN)
    assert (summary["looked_at"], summary["emailed"], claude) == (0, False, [])


def test_the_studio_shows_and_updates_them(
    week: Session, claude: list[Any], client: TestClient, trainer_headers: dict[str, str]
) -> None:
    run.run_all(week, get_settings(), RUN)
    listed = client.get("/api/v1/trainings", headers=trainer_headers).json()
    assert {t["training_id"]: t["recommendations"] for t in listed} == {"big4": 1, "walk": 0}
    body = client.get("/api/v1/trainings/big4/recommendations", headers=trainer_headers).json()
    (rec,) = body["open"]
    assert rec["title"] == REC["title"] and body["last_run"]["calls"] == 22 and body["handled"] == []
    done = client.patch(f"/api/v1/recommendations/{rec['id']}", headers=trainer_headers,
                        json={"status": "done", "note": "Reworded C in v2"})  # fmt: skip
    assert done.status_code == 200 and done.json()["status_by"]
    body = client.get("/api/v1/trainings/big4/recommendations", headers=trainer_headers).json()
    assert body["open"] == [] and body["handled"][0]["status_note"] == "Reworded C in v2"
    assert client.get("/api/v1/trainings", headers=trainer_headers).json()[0]["recommendations"] == 0
    bad = client.patch(
        f"/api/v1/recommendations/{rec['id']}", headers=trainer_headers, json={"status": "gone"}
    )
    assert bad.status_code == 422


def test_the_email_preview_renders(week: Session, claude: list[Any]) -> None:
    run.run_all(week, get_settings(), RUN)
    subject, html = run.build_email(week, get_settings(), RUN)
    assert subject.endswith("Wed, Sep 30") and "The Big 4" in html and "1 content recommendation for" in html


def test_the_writer_doesnt_force_the_tool(monkeypatch):
    """Sonnet 5.5 answers a forced tool_choice with a 400 (2026-11-06), so the call leaves it to the model."""
    import anthropic

    sent: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            sent.update(kwargs)
            block = SimpleNamespace(type="tool_use", name="submit", input={"recommendations": [], "ours": []})
            usage = SimpleNamespace(input_tokens=10, output_tokens=5)
            return SimpleNamespace(content=[block], stop_reason="tool_use", usage=usage)

    monkeypatch.setattr(anthropic, "AnthropicBedrock", FakeClient)
    got, usage = writer.call_claude(get_settings(), {"calls": 1}, [])
    assert sent["tool_choice"] == {"type": "auto"}
    assert "submit tool" in sent["messages"][0]["content"]
    assert got == {"recommendations": [], "ours": []} and usage == {"input_tokens": 10, "output_tokens": 5}


def test_calls_where_nobody_spoke_arent_hangups(week: Session) -> None:
    """A trainee who never said a word (repeated Start taps, no mic) didn't leave because of the welcome."""
    for i in range(3):
        insert(week, "training_sessions", session_id=f"silent{i}", uid=1001, training_id="big4", version_id=1,
               room_name=f"room-silent{i}", started_at=at(24, 40 + i), duration_sec=61, start_point="new",
               client="flutter", outcome="not_passed", end_reason="user_left")  # fmt: skip
    week.flush()
    ev = evidence.gather(week, _f())
    assert ev["left_without_saying_anything"]["calls"] == 3
    assert not any("welcome" in h["where"] for h in ev["hangups_before_passing"])


def test_the_window_doesnt_reach_before_the_grading_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "recommendations_not_before", date(2026, 11, 6))
    assert run.window(settings, date(2026, 11, 6)) is None
    assert run.window(settings, date(2026, 11, 8)) == (date(2026, 11, 6), date(2026, 11, 7))
    assert run.window(settings, date(2026, 11, 20)) == (date(2026, 11, 13), date(2026, 11, 19))
