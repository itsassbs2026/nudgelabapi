"""Every report figure, checked against the hand-built dataset in tests/agent_data.py (SPEC §5, §7)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.agent_data import add_assignments, seed

SEPT = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@pytest.fixture
def data(db_session: Session) -> None:
    seed(db_session)


def get(client: TestClient, headers: dict[str, str], path: str, **params: Any) -> Any:
    response = client.get(f"/api/v1{path}", headers=headers, params={**SEPT, **params})
    assert response.status_code == 200, response.text
    return response.json()


# -- overview ------------------------------------------------------------------------------------------------


def test_overview_activity(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    a = get(client, trainer_headers, "/reports/overview")["activity"]
    assert a["sessions"] == 5  # s1–s5; not the bot (s6), August (s7) or 08-31 local (s8)
    assert a["trainees"] == 4
    assert a["avg_session_seconds"] == 320
    assert a["completions"] == 2  # 1001 big4 (09-06), 1004 walk (09-15)
    assert a["avg_rating"] == 7.0 and a["ratings"] == 2
    assert a["avg_review_score"] == 3.5 and a["reviewed_sessions"] == 2 and a["flagged_sessions"] == 1
    assert a["cost"] == 1.5 and a["cost_per_completion"] == 0.75


def test_overview_funnel_started_basis(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    fn = get(client, trainer_headers, "/reports/overview")["funnel"]
    assert fn == {
        "basis": "started",
        "cohort": 4,
        "started": 4,
        "walkthrough_done": 3,
        "quiz_attempted": 2,
        "completed": 2,
        "completion_rate": 0.5,
    }


def test_overview_daily_series_uses_local_days(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    daily = {d["date"]: d for d in get(client, trainer_headers, "/reports/overview")["daily"]}
    assert len(daily) == 30
    assert daily["2026-09-01"]["sessions"] == 0  # s8 is 22:00 on 08-31 in Chicago
    assert daily["2026-09-05"] == {"date": "2026-09-05", "sessions": 1, "completions": 0, "cost": 0.5}
    assert daily["2026-09-06"]["completions"] == 1


def test_august_window_sees_the_late_august_session(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    a = get(client, trainer_headers, "/reports/overview", date_from="2026-08-31", date_to="2026-08-31")[
        "activity"
    ]
    assert a["sessions"] == 1  # s8, 03:00 UTC 09-01 = 22:00 08-31 local


def test_bots_are_excluded_unless_an_admin_asks(
    client: TestClient, admin_headers: dict[str, str], trainer_headers: dict[str, str], data: None
) -> None:
    assert get(client, admin_headers, "/reports/overview", include_bots="true")["activity"]["sessions"] == 6
    denied = client.get(
        "/api/v1/reports/overview", headers=trainer_headers, params={**SEPT, "include_bots": "true"}
    )
    assert denied.status_code == 403


def test_org_filter_uses_store_at_session(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    assert get(client, trainer_headers, "/reports/overview", store_id="S1")["activity"]["sessions"] == 3
    east = get(client, trainer_headers, "/reports/overview", region_id=2)
    assert east["activity"]["sessions"] == 1 and east["funnel"]["cohort"] == 1


def test_training_and_type_filters(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    assert get(client, trainer_headers, "/reports/overview", training_id="walk")["activity"]["sessions"] == 1
    assert (
        get(client, trainer_headers, "/reports/overview", completion_type="quiz")["activity"]["sessions"] == 4
    )


def test_funnel_switches_to_assigned_basis(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, data: None
) -> None:
    add_assignments(db_session)
    fn = get(client, trainer_headers, "/reports/overview", training_id="big4")["funnel"]
    assert fn["basis"] == "assigned"
    assert (fn["cohort"], fn["started"], fn["completed"]) == (3, 2, 1)
    assert fn["completion_rate"] == round(1 / 3, 4)


def test_bad_date_range(client: TestClient, trainer_headers: dict[str, str]) -> None:
    bad = client.get(
        "/api/v1/reports/overview",
        headers=trainer_headers,
        params={"date_from": "2026-09-30", "date_to": "2026-09-01"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_date_range"


# -- trainings -----------------------------------------------------------------------------------------------


def test_trainings_table(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    rows = {r["training_id"]: r for r in get(client, trainer_headers, "/reports/trainings")["items"]}
    big4 = rows["big4"]
    assert big4["completion_type"] == "quiz" and big4["active_version"] == "v1"
    assert big4["sessions"] == 4 and big4["trainees"] == 3 and big4["completions"] == 1
    assert big4["funnel"]["cohort"] == 3 and big4["avg_rating"] == 7.0 and big4["cost"] == 1.4
    assert rows["walk"]["completions"] == 1 and rows["walk"]["funnel"]["completion_rate"] == 1.0


def test_training_detail(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    d = get(client, trainer_headers, "/reports/trainings/big4")
    assert d["funnel"]["cohort"] == 3 and d["funnel"]["completed"] == 1
    assert {s["stage"]: s["trainees"] for s in d["dropoff"]} == {"topic_2": 1, "quiz": 1}
    topics = {t["topic_number"]: t for t in d["topics"]}
    assert (
        topics[1]["title"] == "Big 4 topic 1"
        and topics[1]["median_seconds"] == 45
        and topics[1]["samples"] == 2
    )
    assert topics[2]["avg_seconds"] == 120 and topics[3].get("samples") is None
    assert d["ratings"]["distribution"] == {"5": 1, "9": 1} and d["ratings"]["average"] == 7.0
    assert {c["comment"] for c in d["ratings"]["comments"]} == {"Great", "5, a bit long"}
    assert d["reviews"]["distribution"] == {"2": 1, "5": 1} and d["reviews"]["flagged"] == 1
    assert {i["type"] for i in d["reviews"]["top_issue_types"]} == {"long_turn", "repetition"}
    assert d["versions"][0]["sessions"] == 4 and d["versions"][0]["completions"] == 1


def test_unknown_training_is_404(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    assert client.get("/api/v1/reports/trainings/nope", headers=trainer_headers).status_code == 404


def test_question_stats_most_missed_first(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    qs = get(client, trainer_headers, "/reports/trainings/big4/questions")["questions"]
    assert [q["question_number"] for q in qs] == [2, 1]
    q2, q1 = qs
    assert q2["first_try_accuracy"] == 0.0 and q2["trainees"] == 2  # 1001 C, 1003 A on the first try
    assert q2["overall_accuracy"] == round(1 / 3, 4) and q2["correct_option"] == "B"
    assert (
        q1["first_try_accuracy"] == 0.5
        and q1["most_common_wrong_option"] == "B"
        and q1["heard_examples"] == ["be"]
    )


# -- drill-down ----------------------------------------------------------------------------------------------


def test_drilldown_regions(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    out = get(client, trainer_headers, "/reports/drilldown", level="region")
    rows = {r["name"]: r for r in out["rows"]}
    assert out["basis"] == "started"
    west, east = rows["West"], rows["East"]
    assert (west["cohort"], west["completed"], west["sessions"], west["trainees_active"]) == (3, 1, 4, 3)
    assert west["avg_rating"] == 7.0
    assert (east["cohort"], east["completed"], east["completion_rate"]) == (1, 1, 1.0)


def test_drilldown_down_to_employees(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    stores = get(client, trainer_headers, "/reports/drilldown", level="store", parent="100")["rows"]
    assert {r["id"]: r["sessions"] for r in stores} == {"S1": 3, "S2": 1}
    people = get(client, trainer_headers, "/reports/drilldown", level="employee", parent="S1")["rows"]
    assert {r["id"]: (r["cohort"], r["completed"]) for r in people} == {"1001": (1, 1), "1002": (1, 0)}


def test_drilldown_needs_a_parent_below_region(client: TestClient, trainer_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/reports/drilldown", headers=trainer_headers, params={"level": "market"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "parent_required"


# -- employee, cost, feedback, acknowledgments, options ------------------------------------------------------


def test_employee(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    e = client.get("/api/v1/employees/1001", headers=trainer_headers).json()
    assert e["name"] == "Trainee 1001" and e["store"]["region_name"] == "West"
    assert {t["training_id"] for t in e["trainings"]} == {"big4", "walk"}
    big4 = next(t for t in e["trainings"] if t["training_id"] == "big4")
    assert len(big4["answers"]) == 3 and big4["completed_at"] is not None
    assert [s["session_id"] for s in e["sessions"]] == ["s2", "s1", "s7"]  # newest first, no bot session
    assert client.get("/api/v1/employees/424242", headers=trainer_headers).status_code == 404
    bots = client.get("/api/v1/employees/1001", headers=trainer_headers, params={"include_bots": "true"})
    assert bots.status_code == 403


def test_cost(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    c = get(client, trainer_headers, "/reports/cost")
    assert c["total"] == 1.5 and c["sessions"] == 5 and c["per_session"] == 0.3
    assert c["split"] == {"claude": 0.3, "polly": 0.9, "transcribe": 0.3}
    assert c["per_completion"] == 0.75 and c["cache_hit_rate"] == 0.8
    assert {r["id"]: r["cost"] for r in c["by_training"]} == {"big4": 1.4, "walk": 0.1}
    assert c["by_setup"] == [{"id": "standard", "name": "Standard", "sessions": 5, "cost": 1.5}]


def test_feedback_list(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    page = get(client, trainer_headers, "/feedback")
    assert page["total"] == 2 and page["items"][0]["comment"] == "5, a bit long"  # newest first
    assert get(client, trainer_headers, "/feedback", min_rating=6)["total"] == 1
    assert get(client, trainer_headers, "/feedback", search="long")["total"] == 1


def test_acknowledgments_list(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    page = get(client, trainer_headers, "/acknowledgments")
    assert page["total"] == 1 and page["items"][0]["trainee_quote"] == "I understand it"


def test_filter_options(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    o = client.get(
        "/api/v1/reports/filter-options", headers=trainer_headers, params={"district_id": 100}
    ).json()
    assert [r["name"] for r in o["regions"]] == ["East", "West"]
    assert [s["id"] for s in o["stores"]] == ["S1", "S2"]
    assert {t["id"] for t in o["trainings"]} == {"big4", "walk"}


def test_assignments(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, data: None
) -> None:
    add_assignments(db_session)
    page = get(client, trainer_headers, "/assignments")
    assert page["counts"] == {
        "not_started": 1,
        "in_progress": 0,
        "completed": 1,
        "overdue": 1,
    }  # 1003 cancelled
    states = {i["uid"]: i["state"] for i in page["items"]}
    assert states == {1001: "completed", 1002: "overdue", 1005: "not_started"}
    assert [i["uid"] for i in page["items"]][:2] == [1002, 1005]  # soonest due first
    overdue = get(client, trainer_headers, "/assignments", state="overdue")
    assert overdue["total"] == 1 and overdue["items"][0]["name"] == "Trainee 1002"
    assert get(client, trainer_headers, "/assignments", region_id=1)["total"] == 2
