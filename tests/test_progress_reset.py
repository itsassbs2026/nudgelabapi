"""Reset a person's training progress (2026-10-22): archived first, then the person starts brand new; a
pass is cleared from Prime Portal through the portal_clears queue; refused during a call.

Seed (tests/agent_data.py): 1001 passed big4 (v1) and walk; 1002 stopped after topic 1 of big4; 1004 passed
walk; 1005 has no progress. big4 gets a completion key here; walk has none.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import add_assignments, insert, seed

Headers = dict[str, str]


@pytest.fixture
def data(db_session: Session) -> None:
    seed(db_session)
    add_assignments(db_session)
    db_session.execute(text("UPDATE trainings SET completion_key = 'agent_big4' WHERE training_id = 'big4'"))


def reset(client: TestClient, headers: Headers, uid: int, body: dict[str, Any], status: int = 200) -> Any:
    r = client.post(f"/api/v1/employees/{uid}/reset-progress", headers=headers, json=body)
    assert r.status_code == status, r.text
    return r.json()


def progress(db: Session, uid: int, tid: str) -> Any:
    sql = (
        "SELECT status, topics_covered, correct_questions, quiz_attempted, walkthrough_finished_at,"
        " first_started_at, passed_at, version_id, quiz_draw, sessions_count FROM training_progress"
        " WHERE uid = :u AND training_id = :t"
    )
    return db.execute(text(sql), {"u": uid, "t": tid}).one()


def test_reset_one_training_archives_it_and_starts_the_person_brand_new(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    db_session.execute(text("UPDATE training_progress SET quiz_draw = '{\"drawn\": [1]}' WHERE uid = 1001"))
    before = progress(db_session, 1001, "big4")
    assert before.passed_at is not None and before.version_id == 1

    out = reset(client, admin_headers, 1001, {"training_id": "big4", "reason": "Manager asked for a redo"})
    assert out == {
        "reset": [{"training_id": "big4", "title": "The Big 4", "was_passed": True, "portal": "queued"}],
        "skipped": 0,
    }
    after = progress(db_session, 1001, "big4")
    assert after.status == "not_started" and after.passed_at is None and after.first_started_at is None
    assert after.quiz_attempted == 0 and after.walkthrough_finished_at is None and after.version_id is None
    assert (
        after.topics_covered in ("[]", [])
        and after.correct_questions in ("[]", [])
        and after.quiz_draw is None
    )
    assert after.sessions_count == before.sessions_count  # the call history is not touched

    archived = db_session.execute(
        text("SELECT status, passed_at, archived_reason FROM training_progress_history WHERE uid = 1001")
    ).all()
    assert len(archived) == 1 and archived[0].status == "passed" and archived[0].passed_at == before.passed_at
    assert "Manager asked for a redo" in archived[0].archived_reason

    clears = db_session.execute(
        text("SELECT uid, training_id, completion_key, status FROM portal_clears")
    ).all()
    assert [tuple(c) for c in clears] == [(1001, "big4", "agent_big4", "pending")]
    # The other training is untouched.
    assert progress(db_session, 1001, "walk").status == "passed"


def test_reset_all_covers_every_training_with_progress(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    out = reset(client, admin_headers, 1001, {})
    assert [r["training_id"] for r in out["reset"]] == ["big4", "walk"]
    assert {r["training_id"]: r["portal"] for r in out["reset"]} == {"big4": "queued", "walk": "not_linked"}
    assert progress(db_session, 1001, "big4").status == "not_started"
    assert progress(db_session, 1001, "walk").status == "not_started"
    assert db_session.execute(text("SELECT COUNT(*) FROM portal_clears")).scalar() == 1  # only big4 has a key
    assert (
        db_session.execute(text("SELECT COUNT(*) FROM training_progress_history WHERE uid = 1001")).scalar()
        == 2
    )
    # Someone else's progress is untouched.
    assert progress(db_session, 1002, "big4").status == "in_progress"


def test_an_unfinished_training_resets_without_a_portal_clear_for_a_pass_that_never_was(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    out = reset(client, admin_headers, 1002, {"training_id": "big4"})
    assert out["reset"][0]["was_passed"] is False
    assert progress(db_session, 1002, "big4").status == "not_started"


def test_a_completed_assignment_is_reopened_and_its_due_date_kept(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    db_session.execute(
        text(
            "UPDATE training_assignments SET status = 'completed', due_at = '2026-10-30 12:00:00'"
            " WHERE uid = 1001 AND training_id = 'big4'"
        )
    )
    reset(client, admin_headers, 1001, {"training_id": "big4"})
    row = db_session.execute(
        text("SELECT status, due_at FROM training_assignments WHERE uid = 1001 AND training_id = 'big4'")
    ).one()
    assert row.status == "assigned" and row.due_at == dt.datetime(2026, 10, 30, 12, 0)


def test_nothing_to_reset(client: TestClient, admin_headers: Headers, data: None) -> None:
    body = reset(client, admin_headers, 1005, {"training_id": "big4"}, status=404)
    assert body["error"]["code"] == "nothing_to_reset"
    assert reset(client, admin_headers, 1005, {}) == {"reset": [], "skipped": 0}


def test_refused_during_a_call_but_not_for_a_stale_unfinished_session(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    insert(
        db_session,
        "training_sessions",
        session_id="live-1",
        uid=1002,
        training_id="big4",
        started_at=now - dt.timedelta(minutes=5),
        ended_at=None,
    )
    body = reset(client, admin_headers, 1002, {"training_id": "big4"}, status=409)
    assert body["error"]["code"] == "call_in_progress"
    assert progress(db_session, 1002, "big4").status == "in_progress"  # nothing changed
    assert db_session.execute(text("SELECT COUNT(*) FROM training_progress_history")).scalar() == 0
    # A call about another training doesn't block this one; nor does an old session that never closed.
    reset(client, admin_headers, 1002, {"training_id": "walk"}, status=404)  # nothing there: 404, not 409
    db_session.execute(
        text("UPDATE training_sessions SET started_at = :t WHERE session_id = 'live-1'"),
        {"t": now - dt.timedelta(hours=3)},
    )
    reset(client, admin_headers, 1002, {"training_id": "big4"})


def test_trainers_may_reset_by_default_and_an_admin_can_switch_it_off(
    client: TestClient, admin_headers: Headers, trainer_headers: Headers, data: None
) -> None:
    assert client.get("/api/v1/me/permissions", headers=trainer_headers).json()["reset_progress"] is True
    reset(client, trainer_headers, 1002, {"training_id": "big4"})
    switch = {"changes": [{"action": "reset_progress", "role": "trainer", "enabled": False}]}
    assert client.put("/api/v1/admin/permissions", headers=admin_headers, json=switch).status_code == 200
    assert client.get("/api/v1/me/permissions", headers=trainer_headers).json()["reset_progress"] is False
    reset(client, trainer_headers, 1001, {"training_id": "big4"}, status=403)
    reset(client, admin_headers, 1001, {"training_id": "big4"})  # admins always may


def test_the_audit_log_says_who_reset_whom(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    reset(client, admin_headers, 1001, {"training_id": "big4", "reason": "retraining"})
    row = db_session.execute(
        text(
            "SELECT action, target_type, target_id, details FROM dash_audit_log"
            " WHERE action = 'progress_reset'"
        )
    ).one()
    assert (row.action, row.target_type, str(row.target_id)) == ("progress_reset", "employee", "1001")
    details = row.details if isinstance(row.details, dict) else __import__("json").loads(row.details)
    assert details["trainings"] == ["big4"] and details["passed_before"] == ["big4"]
    assert details["portal_clears"] == 1 and details["reason"] == "retraining"


def test_answers_from_before_a_reset_are_not_shown_under_the_new_start(
    client: TestClient, db_session: Session, admin_headers: Headers, data: None
) -> None:
    def shown() -> list[str]:
        body = client.get("/api/v1/employees/1001", headers=admin_headers).json()
        big4 = next(t for t in body["trainings"] if t["training_id"] == "big4")
        return sorted(a["heard_as"] for a in big4["answers"] if a["heard_as"] in ("old", "new"))

    def answer(session_id: str, heard: str, when: dt.datetime) -> None:
        insert(
            db_session,
            "training_sessions",
            session_id=session_id,
            room_name=session_id,
            uid=1001,
            training_id="big4",
            started_at=when,
            ended_at=when + dt.timedelta(minutes=5),
        )
        insert(
            db_session,
            "quiz_answers",
            session_id=session_id,
            uid=1001,
            training_id="big4",
            question_number=1,
            round=1,
            given_option="A",
            is_correct=1,
            heard_as=heard,
            answered_at=when,
        )

    answer("old-session", "old", dt.datetime(2026, 9, 6, 15, 0))
    assert shown() == ["old"]
    reset(client, admin_headers, 1001, {"training_id": "big4"})
    assert shown() == []  # still in the database, not part of the new start
    assert db_session.execute(text("SELECT COUNT(*) FROM quiz_answers WHERE heard_as = 'old'")).scalar() == 1
    answer("new-session", "new", dt.datetime.now(dt.UTC).replace(tzinfo=None))
    assert shown() == ["new"]
