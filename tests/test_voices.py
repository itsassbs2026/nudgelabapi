"""Voices and voice samples (SPEC 10.3): the list, and text read aloud by Polly (replaced here by a fake)."""

from __future__ import annotations

from typing import Any

import pytest
from app.config import get_settings
from app.services import voices
from botocore.exceptions import NoCredentialsError
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import insert, seed

MP3 = b"ID3\x04fake-mp3"


@pytest.fixture
def voice_list(db_session: Session) -> None:
    seed(db_session)  # Matthew
    db_session.connection().execute(
        text("UPDATE training_voices SET sort_order = 60 WHERE voice_id = 'Matthew'")
    )
    insert(db_session, "training_voices", voice_id="Ruth", display_name="Ruth", language_code="en-US",
           gender="Female", is_default=1, sort_order=10)  # fmt: skip
    insert(db_session, "training_voices", voice_id="Amy", display_name="Amy", language_code="en-GB",
           gender="Female", is_active=0, sort_order=80, engine="neural")  # fmt: skip


@pytest.fixture
def polly(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    def fake(settings: Any, voice_id: str, engine: str, words: str) -> bytes:
        calls.append((voice_id, engine, words))
        return MP3

    monkeypatch.setattr(voices, "synthesize", fake)
    return calls


def sample(client: TestClient, headers: dict[str, str], voice: str, words: str) -> Any:
    return client.post(f"/api/v1/voices/{voice}/sample", headers=headers, json={"text": words})


def test_list(client: TestClient, trainer_headers: dict[str, str], voice_list: None) -> None:
    rows = client.get("/api/v1/voices", headers=trainer_headers).json()
    assert [(v["voice_id"], v["is_default"], v["is_active"], v["engine"]) for v in rows] == [
        ("Ruth", True, True, "generative"),
        ("Matthew", False, True, "generative"),
        ("Amy", False, False, "neural"),
    ]
    assert rows[2]["language_code"] == "en-GB"


def test_sample(
    client: TestClient, trainer_headers: dict[str, str], voice_list: None, polly: list[tuple[str, str, str]]
) -> None:
    r = sample(client, trainer_headers, "Amy", "  Hey!   I'm your trainer.\n")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.content == MP3
    assert polly == [("Amy", "neural", "Hey! I'm your trainer.")]  # the voice's own engine; spaces tidied


@pytest.mark.parametrize(
    ("voice", "words", "status", "code"),
    [
        ("Nobody", "Hi there", 404, "not_found"),
        ("Ruth", "x" * 601, 422, "text_too_long"),
        ("Ruth", "   ", 422, "validation_error"),
        ("Ruth;DROP", "Hi", 422, "validation_error"),
    ],
)
def test_sample_refused(
    client: TestClient,
    trainer_headers: dict[str, str],
    voice_list: None,
    polly: list[tuple[str, str, str]],
    voice: str,
    words: str,
    status: int,
    code: str,
) -> None:
    r = sample(client, trainer_headers, voice, words)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code
    assert polly == []


def test_sample_rate_limit(
    client: TestClient,
    trainer_headers: dict[str, str],
    admin_headers: dict[str, str],
    voice_list: None,
    polly: list[tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "voice_sample_rate_limit", "2/minute")
    codes = [sample(client, trainer_headers, "Ruth", "Hi").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert sample(client, admin_headers, "Ruth", "Hi").status_code == 200  # per user


def test_polly_unavailable(
    client: TestClient, trainer_headers: dict[str, str], voice_list: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def synthesize_speech(self, **_: Any) -> Any:
            raise NoCredentialsError()

    monkeypatch.setattr(voices.boto3, "client", lambda *_a, **_k: Broken())
    r = sample(client, trainer_headers, "Ruth", "Hi")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "samples_unavailable"


def test_synthesize_asks_polly_for_mp3(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Stream:
        def read(self) -> bytes:
            return MP3

    class Polly:
        def synthesize_speech(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return {"AudioStream": Stream()}

    monkeypatch.setattr(
        voices.boto3, "client", lambda name, region_name: seen.update(region=region_name) or Polly()
    )
    assert voices.synthesize(get_settings(), "Ruth", "generative", "Hello") == MP3
    assert seen == {
        "region": "us-east-1",
        "Text": "Hello",
        "VoiceId": "Ruth",
        "Engine": "generative",
        "OutputFormat": "mp3",
        "SampleRate": "24000",
    }
