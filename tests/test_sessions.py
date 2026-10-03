"""Session list, session viewer and recording links (SPEC §8.2, Phase 4)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import add_session_details, seed

SEPT = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
BUCKET = "nudgeailab"
FAKE_AWS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


@pytest.fixture
def keys(db_session: Session) -> dict[str, str]:
    seed(db_session)
    return add_session_details(db_session)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch, keys: dict[str, str]) -> Iterator[Any]:
    for name in FAKE_AWS:
        monkeypatch.setenv(name, "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-west-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-1"})
        client.put_object(Bucket=BUCKET, Key=keys["rec-new"], Body=b"OggS fake audio")
        yield client


def sessions(client: TestClient, headers: dict[str, str], **params: Any) -> Any:
    response = client.get("/api/v1/sessions", headers=headers, params={**SEPT, **params})
    assert response.status_code == 200, response.text
    return response.json()


def audit_rows(db: Session, action: str) -> list[Any]:
    sql = "SELECT actor_user_id, target_type, target_id, details, ip FROM dash_audit_log WHERE action = :a"
    return list(db.execute(text(sql), {"a": action}))


# -- list ----------------------------------------------------------------------------------------------------


def test_list_newest_first_without_bots(
    client: TestClient, trainer_headers: dict[str, str], keys: Any
) -> None:
    page = sessions(client, trainer_headers)
    assert page["total"] == 5
    assert [i["session_id"] for i in page["items"]] == ["s5", "s4", "s3", "s2", "s1"]
    s3_row = next(i for i in page["items"] if i["session_id"] == "s3")
    assert s3_row["name"] == "Trainee 1002" and s3_row["store_name"] == "Store S1"
    assert s3_row["rating"] == 5 and s3_row["review_score"] == 2 and s3_row["flagged"] is True
    assert s3_row["cost"] == 0.2 and s3_row["recording"] == "none"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"uid": 1001}, ["s2", "s1"]),
        ({"flagged": "true"}, ["s3"]),
        ({"flagged": "false"}, ["s5", "s4", "s2", "s1"]),
        ({"min_rating": 6}, ["s1"]),
        ({"outcome": "passed"}, ["s2"]),
        ({"end_reason": "completed"}, ["s2"]),
        ({"search": "Trainee 1002"}, ["s3"]),
        ({"search": "1003"}, ["s4"]),
        ({"store_id": "S2"}, ["s4"]),
        ({"page": 2, "page_size": 2}, ["s3", "s2"]),
    ],
)
def test_list_filters(
    client: TestClient,
    trainer_headers: dict[str, str],
    keys: Any,
    params: dict[str, Any],
    expected: list[str],
) -> None:
    assert [i["session_id"] for i in sessions(client, trainer_headers, **params)["items"]] == expected


def test_list_shows_recording_state(client: TestClient, trainer_headers: dict[str, str], keys: Any) -> None:
    today = date.today()
    window = {"date_from": str(today - timedelta(days=150)), "date_to": str(today), "training_id": "walk"}
    states = {i["session_id"]: i["recording"] for i in sessions(client, trainer_headers, **window)["items"]}
    assert (states["rec-new"], states["rec-gone"], states["rec-old"]) == ("available", "available", "expired")
    assert states["s5"] == "none"


# -- detail --------------------------------------------------------------------------------------------------


def test_detail(client: TestClient, trainer_headers: dict[str, str], db_session: Session, keys: Any) -> None:
    response = client.get("/api/v1/sessions/s1", headers=trainer_headers)
    assert response.status_code == 200, response.text
    d = response.json()
    head = d["session"]
    assert head["trainee_name"] == "Trainee 1001" and head["training_title"] == "The Big 4"
    assert (
        head["version_label"] == "v1"
        and head["profile_name"] == "Standard"
        and head["voice_name"] == "Matthew"
    )
    assert head["store"]["region_name"] == "West" and head["recording"] == "none"

    assert [line["seq"] for line in d["transcript"]] == [1, 2, 3, 4]
    assert d["transcript"][2]["interrupted"] is True and d["transcript"][3]["seconds"] == 400

    timeline = [(e["type"], e["seconds"]) for e in d["events"]]
    assert timeline == [
        ("topic_reached", 0),
        ("topic_reached", 60),
        ("guardrail", 120),
        ("topic_reached", 180),
        ("dropped", 240),
        ("quiz_answer", 360),
        ("quiz_answer", 420),
        ("rating", 540),
    ]
    assert d["events"][0]["label"] == "Topic 1: Big 4 topic 1"
    assert d["events"][4]["label"] == "Connection dropped"
    assert d["events"][6]["data"] == {
        "question_number": 2,
        "round": 1,
        "given_option": "C",
        "is_correct": False,
        "heard_as": "see",
    }

    praise, long_turn = d["review"]["issues"]
    assert (praise["seq"], praise["seconds"]) == (3, 70)  # found by its quote
    assert (long_turn["seq"], long_turn["seconds"]) == (None, 300)  # quote not found: from the clock time
    assert d["review"]["score"] == 5 and d["review"]["summary"] == "Went well"
    assert d["feedback"]["rating"] == 9
    assert d["usage"]["cost"] == {"claude": 0.1, "polly": 0.3, "transcribe": 0.1, "total": 0.5}

    (row,) = audit_rows(db_session, "transcript_viewed")
    assert row.target_type == "session" and row.target_id == "s1"


def test_detail_unknown_session(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session
) -> None:
    response = client.get("/api/v1/sessions/nope", headers=trainer_headers)
    assert response.status_code == 404
    assert audit_rows(db_session, "transcript_viewed") == []


def test_detail_rejects_odd_ids(client: TestClient, trainer_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/sessions/a%27b", headers=trainer_headers).status_code == 422


# -- recordings ----------------------------------------------------------------------------------------------


def test_trainer_can_play_a_recording(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, s3: Any, keys: dict[str, str]
) -> None:
    response = client.post("/api/v1/sessions/rec-new/recording-url", headers=trainer_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_type"] == "audio/ogg"
    url = urlparse(body["url"])
    query = parse_qs(url.query)
    assert url.netloc == f"{BUCKET}.s3.us-west-1.amazonaws.com" and url.path == "/" + keys["rec-new"]
    assert query["X-Amz-Expires"] == ["300"]  # the link stops working after 5 minutes
    assert query["response-content-type"] == ["audio/ogg"]
    assert query["response-content-disposition"] == ['inline; filename="rec-new.ogg"']

    (row,) = audit_rows(db_session, "recording_played")
    assert row.target_id == "rec-new" and row.ip == "testclient"
    assert keys["rec-new"] in row.details


@pytest.mark.parametrize(
    ("session_id", "status", "code"),
    [
        ("s1", 404, "recording_missing"),  # never recorded
        ("rec-old", 410, "recording_expired"),  # past the 90-day lifecycle rule
        ("rec-gone", 404, "recording_missing"),  # key set, file not in S3
        ("nope", 404, "not_found"),
    ],
)
def test_recording_not_available(
    client: TestClient,
    trainer_headers: dict[str, str],
    db_session: Session,
    s3: Any,
    session_id: str,
    status: int,
    code: str,
) -> None:
    response = client.post(f"/api/v1/sessions/{session_id}/recording-url", headers=trainer_headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert audit_rows(db_session, "recording_played") == []


def test_forbidden_head_counts_as_missing(
    client: TestClient, trainer_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, keys: Any
) -> None:
    """Without s3:ListBucket, S3 answers 403 for a file that isn't there."""
    from app.services import recordings
    from botocore.exceptions import ClientError

    class Forbidden:
        def head_object(self, **_: Any) -> None:
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    monkeypatch.setattr(recordings, "_client", lambda _settings: Forbidden())
    response = client.post("/api/v1/sessions/rec-new/recording-url", headers=trainer_headers)
    assert response.status_code == 404 and response.json()["error"]["code"] == "recording_missing"


def test_s3_down_is_503(
    client: TestClient, trainer_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, keys: Any
) -> None:
    for name in FAKE_AWS:
        monkeypatch.setenv(name, "testing")
    with mock_aws():  # no bucket at all
        response = client.post("/api/v1/sessions/rec-new/recording-url", headers=trainer_headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "recording_unavailable"
