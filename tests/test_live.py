"""Live sessions (SPEC 7.2 Overview): LiveKit room list, mocked here."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.config import get_settings
from app.services import live
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.agent_data import seed

ROOMS = [
    SimpleNamespace(name="nl-big4-1001-a1b2c3", num_participants=2, creation_time=1_790_000_000),
    SimpleNamespace(
        name="nl-sample_store_safety-1004-d4e5f6", num_participants=2, creation_time=1_790_000_100
    ),
    SimpleNamespace(name="nl-big4-3784-bot339c", num_participants=2, creation_time=1_790_000_200),
    SimpleNamespace(name="some-other-room", num_participants=1, creation_time=1_790_000_300),
]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    seed(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "livekit_url", "wss://example.livekit.cloud")
    monkeypatch.setattr(settings, "livekit_api_key", "key")
    monkeypatch.setattr(settings, "livekit_api_secret", "secret")

    async def fake_rooms(_settings: Any) -> list[Any]:
        return ROOMS

    monkeypatch.setattr(live, "_list_rooms", fake_rooms)


def test_not_configured(client: TestClient, trainer_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/live", headers=trainer_headers).json() == {"configured": False, "rooms": []}


def test_rooms_with_names(client: TestClient, trainer_headers: dict[str, str], configured: None) -> None:
    body = client.get("/api/v1/live", headers=trainer_headers).json()
    assert body["configured"] is True
    rooms = [(r["uid"], r["name"], r["training_id"], r["training_title"]) for r in body["rooms"]]
    assert rooms == [
        (1004, "Trainee 1004", "sample_store_safety", None),  # newest first
        (1001, "Trainee 1001", "big4", "The Big 4"),
    ]
    assert body["rooms"][1]["started_at"] == "2026-09-21T14:13:20"


def test_test_rooms_are_admin_only(
    client: TestClient, trainer_headers: dict[str, str], admin_headers: dict[str, str], configured: None
) -> None:
    denied = client.get("/api/v1/live", headers=trainer_headers, params={"include_bots": "true"})
    assert denied.status_code == 403
    rooms = client.get("/api/v1/live", headers=admin_headers, params={"include_bots": "true"}).json()["rooms"]
    assert [r["is_test"] for r in rooms] == [True, False, False]


def test_livekit_down_is_503(
    client: TestClient, trainer_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    async def broken(_settings: Any) -> list[Any]:
        raise OSError("connection refused")

    monkeypatch.setattr(live, "_list_rooms", broken)
    response = client.get("/api/v1/live", headers=trainer_headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "live_unavailable"
