"""Assigning training from the dashboard (2026-10-07): one person, a CSV of uids, cancelling, due dates, and
the per-role switches on Admin > Permissions.

Seed (tests/agent_data.py): Trainees 1001-1005, all current. The Big 4 (big4) is assigned to 1001 (passed),
1002 and 1005, and cancelled for 1003. 1001 and 1004 passed Store Safety (walk), which nobody is assigned.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import add_assignments, insert, seed

Headers = dict[str, str]
CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture
def data(db_session: Session) -> None:
    seed(db_session)
    add_assignments(db_session)


def rows(db: Session, training_id: str) -> dict[int, Any]:
    sql = text(
        "SELECT uid, status, due_at, assigned_via, assigned_by_user_id, assignment_id"
        " FROM training_assignments WHERE training_id = :t"
    )
    return {r.uid: r for r in db.execute(sql, {"t": training_id})}


def actions(db: Session) -> list[str]:
    sql = (
        "SELECT action FROM dash_audit_log WHERE action LIKE 'assignments%' OR action = 'permissions_changed'"
    )
    return list(db.execute(text(sql + " ORDER BY id")).scalars())


def post(client: TestClient, headers: Headers, path: str, body: dict[str, Any], status: int = 200) -> Any:
    r = client.post(f"/api/v1{path}", headers=headers, json=body)
    assert r.status_code == status, r.text
    return r.json()


def switch(client: TestClient, admin: Headers, action: str, enabled: bool) -> None:
    body = {"changes": [{"action": action, "role": "trainer", "enabled": enabled}]}
    assert client.put("/api/v1/admin/permissions", headers=admin, json=body).status_code == 200


def in_a_week() -> str:
    return (datetime.now(CHICAGO).date() + timedelta(days=7)).isoformat()


# -- permissions ---------------------------------------------------------------------------------------------


def test_default_permissions(client: TestClient, trainer_headers: Headers, admin_headers: Headers) -> None:
    mine = client.get("/api/v1/me/permissions", headers=trainer_headers).json()
    assert mine == {
        "assign_single": True,
        "assign_bulk": False,
        "assign_cancel": True,
        "assign_due_date": True,
    }
    assert all(client.get("/api/v1/me/permissions", headers=admin_headers).json().values())
    table = client.get("/api/v1/admin/permissions", headers=admin_headers).json()
    assert [(r["action"], r["roles"]["trainer"]) for r in table] == [
        ("assign_single", True),
        ("assign_bulk", False),
        ("assign_cancel", True),
        ("assign_due_date", True),
    ]
    assert table[1]["label"] == "Mass upload assignments (CSV)"


def test_admin_switches_a_permission(
    client: TestClient, trainer_headers: Headers, admin_headers: Headers, db_session: Session, data: None
) -> None:
    upload = {"csv": "uid\n1004\n", "file_name": "q4.csv", "training_ids": ["big4"]}
    refused = post(client, trainer_headers, "/assignments/check", upload, 403)
    assert "isn't allowed to upload" in refused["error"]["message"]

    switch(client, admin_headers, "assign_bulk", True)
    switch(client, admin_headers, "assign_bulk", True)  # no change, no second audit row
    assert client.get("/api/v1/me/permissions", headers=trainer_headers).json()["assign_bulk"] is True
    assert post(client, trainer_headers, "/assignments/check", upload)["counts"]["assign"] == 1

    switch(client, admin_headers, "assign_single", False)
    one = {"uid": 1004, "training_ids": ["big4"]}
    post(client, trainer_headers, "/assignments", one, 403)
    assert client.get("/api/v1/assignments/people?q=Trainee", headers=trainer_headers).status_code == 403
    assert actions(db_session) == ["permissions_changed", "permissions_changed"]

    bad = {"changes": [{"action": "assign_bulk", "role": "admin", "enabled": False}]}
    r = client.put("/api/v1/admin/permissions", headers=admin_headers, json=bad)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unknown_role")
    assert client.put("/api/v1/admin/permissions", headers=trainer_headers, json=bad).status_code == 403


# -- one person ----------------------------------------------------------------------------------------------


def test_find_people_and_trainings(
    client: TestClient, trainer_headers: Headers, db_session: Session, data: None
) -> None:
    insert(db_session, "v_users_all", uid=1006, name="Trainee 1006", status=0, store_id="S1", job_title="RSC")
    people = client.get("/api/v1/assignments/people?q=Trainee", headers=trainer_headers).json()
    assert [p["uid"] for p in people][-1] == 1006  # people who've left come last
    assert people[0] == {
        "uid": 1001, "name": "Trainee 1001", "is_active": True, "job_title": "RSC", "store_id": "S1",
        "store_name": "Store S1",
    }  # fmt: skip
    by_uid = client.get("/api/v1/assignments/people?q=1003", headers=trainer_headers).json()
    assert [p["uid"] for p in by_uid] == [1003]

    db_session.execute(text("UPDATE trainings SET app_status = 'archived' WHERE training_id = 'walk'"))
    listed = client.get("/api/v1/assignments/trainings", headers=trainer_headers).json()
    assert [(t["training_id"], t["hidden"]) for t in listed] == [("walk", True), ("big4", False)]
    db_session.execute(text("UPDATE trainings SET status = 'retired' WHERE training_id = 'walk'"))
    listed = client.get("/api/v1/assignments/trainings", headers=trainer_headers).json()
    assert [t["training_id"] for t in listed] == ["big4"]  # archived trainings can't be assigned


def test_assign_one_person(
    client: TestClient, trainer_headers: Headers, db_session: Session, data: None
) -> None:
    body = {"uid": 1004, "training_ids": ["big4", "walk"], "due_date": in_a_week()}
    check = post(client, trainer_headers, "/assignments/check", body)
    assert check["applied"] is False and check["person"]["name"] == "Trainee 1004"
    assert check["counts"] == {
        "assign": 2, "reactivate": 0, "reason_changed": 0, "already": 0, "problems": 0, "warnings": 1,
        "duplicates": 0,
    }  # fmt: skip
    assert check["warnings"] == [
        {"uid": 1004, "name": "Trainee 1004", "training_id": "walk",
         "message": "Already passed this training, so it will show as completed."},
    ]  # fmt: skip
    assert rows(db_session, "walk") == {}  # the check changes nothing

    done = post(client, trainer_headers, "/assignments", body)
    assert done["applied"] is True and done["counts"]["assign"] == 2
    trainer_id = db_session.execute(text("SELECT id FROM dash_users WHERE role = 'trainer'")).scalar_one()
    made = rows(db_session, "big4")[1004]
    assert (made.status, made.assigned_via, made.assigned_by_user_id) == ("assigned", "dashboard", trainer_id)
    # Due at the end of that day in Chicago, stored in UTC.
    due = datetime.fromisoformat(str(body["due_date"])).replace(hour=23, minute=59, second=59, tzinfo=CHICAGO)
    assert made.due_at.replace(microsecond=0) == due.astimezone(UTC).replace(tzinfo=None)

    again = post(client, trainer_headers, "/assignments/check", body)
    assert (again["counts"]["assign"], again["counts"]["already"]) == (0, 2)
    assert [(t["training_id"], t["already"]) for t in again["trainings"]] == [("big4", 1), ("walk", 1)]
    assert actions(db_session) == ["assignments_added"]
    details = db_session.execute(
        text("SELECT details, target_id FROM dash_audit_log WHERE action = 'assignments_added'")
    ).one()
    assert details.target_id == "1004" and '"via": "dashboard"' in details.details


def test_a_cancelled_assignment_comes_back(
    client: TestClient, trainer_headers: Headers, db_session: Session, data: None
) -> None:
    done = post(client, trainer_headers, "/assignments", {"uid": 1003, "training_ids": ["big4"]})
    assert done["counts"]["reactivate"] == 1 and done["trainings"][0]["reactivate"] == 1
    back = rows(db_session, "big4")[1003]
    assert (back.status, back.assigned_via, back.due_at) == ("assigned", "dashboard", None)


def test_people_who_cant_be_assigned(
    client: TestClient, trainer_headers: Headers, db_session: Session, data: None
) -> None:
    insert(db_session, "v_users_all", uid=1006, name="Trainee 1006", status=0, store_id="S1", job_title="RSC")
    left = post(client, trainer_headers, "/assignments/check", {"uid": 1006, "training_ids": ["walk"]})
    assert left["problems"] == [
        {"row": None, "value": "1006", "message": "Trainee 1006 has left the company."}
    ]
    assert left["people"] == 0 and left["person"] is None
    nobody = post(client, trainer_headers, "/assignments", {"uid": 99999, "training_ids": ["walk"]})
    assert (
        nobody["problems"][0]["message"] == "No employee with this uid." and nobody["counts"]["assign"] == 0
    )
    assert rows(db_session, "walk") == {}


def test_bad_trainings_and_dates(client: TestClient, trainer_headers: Headers, data: None) -> None:
    r = post(client, trainer_headers, "/assignments/check", {"uid": 1004, "training_ids": ["nope"]}, 422)
    assert r["error"]["code"] == "training_not_assignable" and r["error"]["details"] == {
        "training_ids": ["nope"]
    }
    yesterday = (datetime.now(CHICAGO).date() - timedelta(days=1)).isoformat()
    body = {"uid": 1004, "training_ids": ["big4"], "due_date": yesterday}
    assert post(client, trainer_headers, "/assignments", body, 422)["error"]["code"] == "due_date_past"
    body["due_date"] = date(2099, 1, 1).isoformat()
    assert post(client, trainer_headers, "/assignments", body, 422)["error"]["code"] == "due_date_far"
    both = {"uid": 1004, "csv": "uid\n1\n", "training_ids": ["big4"]}
    assert client.post("/api/v1/assignments/check", headers=trainer_headers, json=both).status_code == 422


def test_another_version_of_the_same_portal_training(
    client: TestClient, trainer_headers: Headers, db_session: Session, data: None
) -> None:
    db_session.execute(
        text("UPDATE trainings SET completion_key = 'K1' WHERE training_id IN ('big4', 'walk')")
    )
    check = post(client, trainer_headers, "/assignments/check", {"uid": 1005, "training_ids": ["walk"]})
    assert (
        check["warnings"][0]["message"] == "Also has The Big 4, another version of the same Portal training."
    )
    both = post(
        client, trainer_headers, "/assignments/check", {"uid": 1004, "training_ids": ["big4", "walk"]}
    )
    assert {w["training_id"] for w in both["warnings"] if "another version" in w["message"]} == {
        "big4",
        "walk",
    }


# -- mass upload ---------------------------------------------------------------------------------------------


def test_upload_a_csv(client: TestClient, admin_headers: Headers, db_session: Session, data: None) -> None:
    csv = "﻿UID,Name\n1001,A\n1002,B\n1002,B again\nabc,C\n,,\n99999,D\n1004.0,E\n"
    body = {"csv": csv, "file_name": "october.csv", "training_ids": ["walk"]}
    check = post(client, admin_headers, "/assignments/check", body)
    assert check["counts"] == {
        "assign": 3, "reactivate": 0, "reason_changed": 0, "already": 0, "problems": 2, "warnings": 2,
        "duplicates": 1,
    }  # fmt: skip
    assert check["problems"] == [
        {"row": 5, "value": "abc", "message": "Not a uid (a uid is a whole number)."},
        {"row": 7, "value": "99999", "message": "No employee with this uid."},
    ]
    assert check["person"] is None and check["people"] == 3

    done = post(client, admin_headers, "/assignments", body)
    assert done["applied"] is True
    made = rows(db_session, "walk")
    assert sorted(made) == [1001, 1002, 1004] and {r.assigned_via for r in made.values()} == {"upload"}
    details = db_session.execute(
        text("SELECT details FROM dash_audit_log WHERE action = 'assignments_added'")
    )
    assert '"file_name": "october.csv"' in details.scalar_one()


def test_upload_formats_and_limits(client: TestClient, admin_headers: Headers, data: None) -> None:
    def check(csv: str, status: int = 200) -> Any:
        return post(
            client, admin_headers, "/assignments/check", {"csv": csv, "training_ids": ["walk"]}, status
        )

    assert check("1002\n1005\n")["counts"]["assign"] == 2  # no header row
    assert check("store,uid\nS1,1002\n")["counts"]["assign"] == 1
    assert check("name\nBob\n", 422)["error"]["code"] == "no_uid_column"
    assert check("uid\n", 422)["error"]["code"] == "file_empty"
    assert check("", 422)["error"]["code"] == "file_empty"
    big = "uid\n" + "\n".join(str(n) for n in range(1, 5002))
    assert check(big, 422)["error"]["code"] == "file_too_big"


# -- cancel and due dates ------------------------------------------------------------------------------------


def test_cancel_assignments(
    client: TestClient, trainer_headers: Headers, admin_headers: Headers, db_session: Session, data: None
) -> None:
    ids = {uid: r.assignment_id for uid, r in rows(db_session, "big4").items()}
    body = {"assignment_ids": [ids[1001], ids[1002], ids[1003], 999999]}
    out = post(client, trainer_headers, "/assignments/cancel", body)
    assert out == {"cancelled": 1, "already_cancelled": 1, "passed": 1, "not_found": 1}
    assert {uid: r.status for uid, r in rows(db_session, "big4").items()} == {
        1001: "assigned", 1002: "cancelled", 1003: "cancelled", 1005: "assigned"
    }  # fmt: skip
    listed = client.get("/api/v1/assignments?any_date=true", headers=trainer_headers).json()
    assert {i["uid"] for i in listed["items"]} == {1001, 1005}
    assert {i["assignment_id"] for i in listed["items"]} == {ids[1001], ids[1005]}
    assert actions(db_session) == ["assignments_cancelled"]

    switch(client, admin_headers, "assign_cancel", False)
    post(client, trainer_headers, "/assignments/cancel", {"assignment_ids": [ids[1005]]}, 403)


def test_change_due_dates(
    client: TestClient, trainer_headers: Headers, admin_headers: Headers, db_session: Session, data: None
) -> None:
    ids = {uid: r.assignment_id for uid, r in rows(db_session, "big4").items()}
    body = {"assignment_ids": [ids[1001], ids[1002], ids[1003], ids[1005]], "due_date": in_a_week()}
    out = post(client, trainer_headers, "/assignments/due-date", body)
    assert (out["updated"], out["skipped"], out["not_found"]) == (2, 2, 0)  # passed and cancelled kept
    due = rows(db_session, "big4")
    assert due[1002].due_at == due[1005].due_at and due[1001].due_at is None

    cleared = post(
        client, trainer_headers, "/assignments/due-date", {"assignment_ids": [ids[1002]], "due_date": None}
    )
    assert cleared["updated"] == 1 and rows(db_session, "big4")[1002].due_at is None
    assert actions(db_session) == ["assignments_due_date", "assignments_due_date"]

    switch(client, admin_headers, "assign_due_date", False)
    post(client, trainer_headers, "/assignments/due-date", body, 403)


# -- Role Play: the reason picks the track (docs/ROLEPLAY.md) ------------------------------------------------


def roleplay_training(db: Session) -> None:
    from pathlib import Path

    content = (Path(__file__).parent / "fixtures" / "content" / "win_every_customer.json").read_text(
        encoding="utf-8"
    )
    insert(
        db,
        "trainings",
        training_id="wec",
        title="Win Every Customer",
        status="active",
        completion_type="roleplay",
    )
    version = {"version_id": 50, "training_id": "wec", "version_label": "v1", "content_hash": "c" * 64}
    insert(db, "training_versions", **version, content=content)
    db.execute(text("UPDATE trainings SET active_version_id = 50 WHERE training_id = 'wec'"))


def test_a_roleplay_is_assigned_with_a_reason(
    client: TestClient, admin_headers: Headers, db_session: Session, data: None
) -> None:
    roleplay_training(db_session)
    listed = {
        t["training_id"]: t for t in client.get("/api/v1/assignments/trainings", headers=admin_headers).json()
    }
    assert listed["wec"]["completion_type"] == "roleplay" and listed["wec"]["default_track"] == "general"
    assert {"id": "billing", "name": "Billing"} in listed["wec"]["tracks"] and listed["big4"]["tracks"] == []

    bad = post(
        client,
        admin_headers,
        "/assignments",
        {"uid": 1004, "training_ids": ["wec"], "reason": "Rudeness"},
        422,
    )
    assert bad["error"]["code"] == "reason_unknown" and "Billing" in bad["error"]["message"]

    done = post(
        client,
        admin_headers,
        "/assignments",
        {"uid": 1004, "training_ids": ["wec", "walk"], "reason": " billing "},
    )
    assert done["reason"] == "Billing" and done["counts"]["assign"] == 2
    flags = dict(
        db_session.execute(
            text("SELECT training_id, ai_flag FROM training_assignments WHERE uid = 1004")
        ).all()
    )
    assert flags == {"wec": "Billing", "walk": None}  # only the roleplay keeps a reason

    # Assigned again for another reason: their next session starts fresh on the new track.
    again = post(
        client,
        admin_headers,
        "/assignments/check",
        {"uid": 1004, "training_ids": ["wec"], "reason": "conduct"},
    )
    assert again["counts"]["reason_changed"] == 1 and again["counts"]["already"] == 0
    assert "starts fresh" in again["warnings"][0]["message"]
    post(client, admin_headers, "/assignments", {"uid": 1004, "training_ids": ["wec"], "reason": "conduct"})
    flag = db_session.execute(
        text("SELECT ai_flag FROM training_assignments WHERE uid = 1004 AND training_id = 'wec'")
    )
    assert flag.scalar_one() == "Conduct"
    same = post(
        client,
        admin_headers,
        "/assignments/check",
        {"uid": 1004, "training_ids": ["wec"], "reason": "Conduct"},
    )
    assert (same["counts"]["already"], same["counts"]["reason_changed"]) == (1, 0)
