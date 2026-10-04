"""Preview calls (SPEC 10.5, Phase 15): a LiveKit token whose metadata carries a signed preview pass for one
version. LiveKit's room list is replaced by a fake; the token itself is decoded and checked."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import jwt as pyjwt
import pytest
from app.config import get_settings
from app.services import live
from app.studio import preview
from app.utils.errors import ApiError
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import insert
from tests.test_studio import big4_content, new_draft

LK_SECRET = "lk-secret-" + "y" * 32
PREVIEW_SECRET = "p" * 40
# The same vector is in the agent's tests (nudgelab/tests/test_preview.py): both sides sign identically.
VECTOR = "99909847d25c813f79856445745a7f0cd096b44df07d3cf66c0cec607877956a"


@pytest.fixture
def studio_data(db_session: Session) -> None:
    """seed(): big4 v1 (active) with real content; walk v2 with none."""
    from tests.agent_data import seed

    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(big4_content())},
    )


@pytest.fixture
def rooms(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    settings = get_settings()
    monkeypatch.setattr(settings, "livekit_url", "wss://example.livekit.cloud")
    monkeypatch.setattr(settings, "livekit_api_key", "lk-key")
    monkeypatch.setattr(settings, "livekit_api_secret", LK_SECRET)
    monkeypatch.setattr(settings, "preview_secret", PREVIEW_SECRET)
    open_rooms: list[Any] = []

    async def fake_rooms(_settings: Any) -> list[Any]:
        return open_rooms

    monkeypatch.setattr(live, "fetch_rooms", fake_rooms)
    return open_rooms


def call(client: TestClient, headers: dict[str, str], version_id: int = 1, **body: Any) -> Any:
    return client.post(f"/api/v1/versions/{version_id}/preview-call", headers=headers, json=body)


def my_id(client: TestClient, headers: dict[str, str]) -> int:
    return int(client.get("/api/v1/me", headers=headers).json()["id"])


def claims_of(body: dict[str, Any]) -> dict[str, Any]:
    decoded: dict[str, Any] = pyjwt.decode(
        body["participant_token"], LK_SECRET, algorithms=["HS256"], options={"verify_aud": False}
    )
    return decoded


def test_signature_matches_the_agent() -> None:
    assert preview.sign("k" * 32, 42, "big4", 900003, "beginning", 1791234567) == VECTOR


def test_preview_token(
    client: TestClient,
    trainer_headers: dict[str, str],
    studio_data: None,
    rooms: list[Any],
    db_session: Session,
) -> None:
    uid = preview.PREVIEW_UID_BASE + my_id(client, trainer_headers)
    before = int(time.time())
    r = call(client, trainer_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["server_url"], body["expires_in"], body["version_id"], body["training_id"]) == (
        "wss://example.livekit.cloud", 1200, 1, "big4"
    )  # fmt: skip
    assert body["room_name"].startswith(f"pv-big4-{uid}-")  # not nl-: the Live page and reports skip it

    claims = claims_of(body)
    assert claims["video"]["room"] == body["room_name"]
    assert claims["sub"].startswith("preview-")
    assert before + 1190 <= claims["exp"] <= before + 1210
    (agent,) = claims["roomConfig"]["agents"]
    assert agent["agentName"] == "nudgelab-trainer"
    meta = json.loads(agent["metadata"])
    p = meta.pop("preview")
    assert meta == {"uid": uid, "training_id": "big4", "client": "preview", "first_name": "User"}
    assert (p["version_id"], p["training_id"], p["uid"], p["start"]) == (1, "big4", uid, "beginning")
    assert before + 1190 <= p["exp"] <= before + 1210
    assert p["sig"] == preview.sign(PREVIEW_SECRET, 1, "big4", uid, "beginning", p["exp"])

    audit = db_session.execute(
        text("SELECT target_type, target_id FROM dash_audit_log WHERE action = 'preview_call'")
    ).one()
    assert (audit.target_type, audit.target_id) == ("version", "1")


def test_choices_reach_the_agent(
    client: TestClient,
    admin_headers: dict[str, str],
    studio_data: None,
    rooms: list[Any],
    db_session: Session,
) -> None:
    insert(db_session, "training_voices", voice_id="Ruth", display_name="Ruth", language_code="en-US",
           gender="Female")  # fmt: skip
    r = call(client, admin_headers, start="after_topics", voice="ruth", profile="standard",
             trainer_name="Dana Smith", location_type="aia_only")  # fmt: skip
    assert r.status_code == 200, r.text
    meta = json.loads(claims_of(r.json())["roomConfig"]["agents"][0]["metadata"])
    assert meta["preview"]["start"] == "after_topics"
    assert (meta["voice"], meta["profile"], meta["trainer_name"], meta["location_type"]) == (
        "Ruth", "standard", "Dana", "aia_only"
    )  # fmt: skip


def test_a_draft_can_be_previewed(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None, rooms: list[Any]
) -> None:
    draft = new_draft(client, trainer_headers)  # a copy of v1, unpublished
    r = call(client, trainer_headers, draft["version_id"])
    assert r.status_code == 200, r.text
    assert (
        json.loads(claims_of(r.json())["roomConfig"]["agents"][0]["metadata"])["preview"]["version_id"]
        == (draft["version_id"])
    )


@pytest.mark.parametrize(
    ("version", "body", "status", "code"),
    [
        (999999, {}, 404, "not_found"),
        (2, {}, 422, "no_content"),  # walk v1 has no content in the database
        (1, {"voice": "Nobody"}, 422, "unknown_voice"),
        (1, {"profile": "nobody"}, 422, "unknown_setup"),
        (1, {"trainer_name": "R2D2"}, 422, "bad_trainer_name"),
        (1, {"start": "passed"}, 422, "validation_error"),
        (1, {"location_type": "moon"}, 422, "validation_error"),
        (1, {"uid": 3784}, 422, "validation_error"),  # the uid is never the caller's to choose
    ],
)
def test_refused(
    client: TestClient,
    trainer_headers: dict[str, str],
    studio_data: None,
    rooms: list[Any],
    version: int,
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    r = call(client, trainer_headers, version, **body)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)


def test_errors_block_a_preview(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None, rooms: list[Any]
) -> None:
    blank = client.post("/api/v1/trainings/big4/versions", headers=trainer_headers, json={"source": "blank"})
    r = call(client, trainer_headers, blank.json()["version_id"])
    assert (r.status_code, r.json()["error"]["code"]) == (422, "has_errors")
    assert r.json()["error"]["details"]["errors"] > 0


def test_one_preview_at_a_time(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None, rooms: list[Any]
) -> None:
    uid = preview.PREVIEW_UID_BASE + my_id(client, trainer_headers)
    rooms.append(SimpleNamespace(name=f"nl-big4-{uid}-abc123"))  # not a preview room
    rooms.append(SimpleNamespace(name=f"pv-big4-{uid + 1}-abc123"))  # someone else's preview
    assert call(client, trainer_headers).status_code == 200
    rooms.append(SimpleNamespace(name=f"pv-walk-{uid}-def456"))
    r = call(client, trainer_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "preview_running")


def test_not_available(
    client: TestClient,
    trainer_headers: dict[str, str],
    studio_data: None,
    rooms: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "preview_secret", "too-short")
    r = call(client, trainer_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "previews_unavailable")

    monkeypatch.setattr(get_settings(), "preview_secret", PREVIEW_SECRET)

    async def broken(_settings: Any) -> list[Any]:
        raise ApiError(503, "live_unavailable", "x")

    monkeypatch.setattr(live, "fetch_rooms", broken)
    r = call(client, trainer_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "previews_unavailable")


def test_rate_limit(
    client: TestClient,
    trainer_headers: dict[str, str],
    studio_data: None,
    rooms: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "preview_rate_limit", "2/minute")
    codes = [call(client, trainer_headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_preview_uid_never_collides(make_user: Callable[..., Any]) -> None:
    assert preview.preview_uid(1) == 900001
