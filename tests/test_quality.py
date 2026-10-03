"""Quality queue (SPEC §8.2, Phase 5). In the dataset, the AI review flagged s3 (long_turn, repetition)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import seed

SEPT = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@pytest.fixture
def data(db_session: Session) -> None:
    seed(db_session)


def queue(client: TestClient, headers: dict[str, str], **params: Any) -> Any:
    response = client.get("/api/v1/quality", headers=headers, params={**SEPT, **params})
    assert response.status_code == 200, response.text
    return response.json()


def patch(client: TestClient, headers: dict[str, str], session_id: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/api/v1/quality/{session_id}", headers=headers, json=body)


def test_flagged_sessions_start_open(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    page = queue(client, trainer_headers)
    assert page["counts"] == {"open": 1, "reviewed": 0, "dismissed": 0}
    (item,) = page["items"]
    assert item["session_id"] == "s3" and item["name"] == "Trainee 1002" and item["score"] == 2
    assert item["issue_types"] == ["long_turn", "repetition"] and item["issue_count"] == 2
    assert item["status"] == "open" and item["resolution"] is None and item["assignee_name"] is None


def test_issue_type_filter(client: TestClient, trainer_headers: dict[str, str], data: None) -> None:
    assert queue(client, trainer_headers, issue_type="repetition")["total"] == 1
    assert queue(client, trainer_headers, issue_type="tone")["total"] == 0


def test_work_an_item(
    client: TestClient,
    trainer_headers: dict[str, str],
    db_session: Session,
    make_user: Any,
    data: None,
) -> None:
    helper = make_user(email="helper@example.com")
    response = patch(
        client,
        trainer_headers,
        "s3",
        {
            "status": "reviewed",
            "resolution": "script_changed",
            "note": "Shortened topic 2",
            "assignee_user_id": helper.id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "reviewed" and response.json()["note"] == "Shortened topic 2"

    page = queue(client, trainer_headers)
    assert page["counts"] == {"open": 0, "reviewed": 1, "dismissed": 0}
    item = page["items"][0]
    assert item["resolution"] == "script_changed" and item["assignee_name"] == helper.full_name
    assert item["updated_by_name"] is not None
    assert queue(client, trainer_headers, status="open")["total"] == 0
    assert queue(client, trainer_headers, assignee_user_id=helper.id)["total"] == 1

    # Only the fields sent change; null clears.
    response = patch(client, trainer_headers, "s3", {"note": None})
    assert response.json()["note"] is None and response.json()["resolution"] == "script_changed"

    rows = list(
        db_session.execute(
            text("SELECT details FROM dash_audit_log WHERE action = 'quality_updated'")
        ).scalars()
    )
    assert len(rows) == 2
    first = rows[0] if isinstance(rows[0], dict) else json.loads(rows[0])
    assert first["before"]["status"] == "open" and first["after"]["status"] == "reviewed"


def test_session_viewer_shows_queue_state(
    client: TestClient, trainer_headers: dict[str, str], data: None
) -> None:
    patch(client, trainer_headers, "s3", {"status": "dismissed", "resolution": "no_action"})
    assert (
        client.get("/api/v1/sessions/s3", headers=trainer_headers).json()["quality"]["status"] == "dismissed"
    )
    assert client.get("/api/v1/sessions/s1", headers=trainer_headers).json()["quality"] is None


@pytest.mark.parametrize(
    ("session_id", "body", "status", "code"),
    [
        ("s1", {"status": "reviewed"}, 404, "not_in_queue"),  # reviewed but not flagged
        ("nope", {"status": "reviewed"}, 404, "not_in_queue"),
        ("s3", {"assignee_user_id": 999999}, 422, "invalid_assignee"),
        ("s3", {"status": "closed"}, 422, "validation_error"),
        ("s3", {"resolution": "shrug"}, 422, "validation_error"),
        ("s3", {"surprise": 1}, 422, "validation_error"),
    ],
)
def test_bad_updates(
    client: TestClient,
    trainer_headers: dict[str, str],
    data: None,
    session_id: str,
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    response = patch(client, trainer_headers, session_id, body)
    assert response.status_code == status and response.json()["error"]["code"] == code
