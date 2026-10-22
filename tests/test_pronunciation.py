"""Check pronunciation (2026-10-23): the acronyms in a draft, said the way a call says them.

CASES is shared with the agent's tests/test_acronyms.py: the rule must stay identical in both repos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.services import voices
from app.studio import pronunciation
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.agent_data import insert, seed

CASES = [
    ("The PPVGA matters", "The P P V G A matters"),
    ("AIP and HTP", "A I P and H T P"),
    ("Sold by AT&T", "Sold by AT&T"),  # AT&T is left alone
    ("AT&TX", "AT&TX"),
    ("A plan", "A plan"),  # one capital is a word
    ("5G and WiFi", "5G and WiFi"),
    ("U.S. stores", "U.S. stores"),
    ("ABCDEF fits", "A B C D E F fits"),  # six letters
    ("ABCDEFG is too long", "ABCDEFG is too long"),  # seven are left alone
    ("x-PSQ, (MST)", "x-P S Q, (M S T)"),
    ("iPhone", "iPhone"),
    ("McDonald", "McDonald"),
]
BIG4 = json.loads((Path(__file__).parent / "fixtures" / "content" / "big4.json").read_text(encoding="utf-8"))
MP3 = b"ID3\x04fake-mp3"


@pytest.mark.parametrize(("text", "said"), CASES)
def test_the_call_rule(text: str, said: str) -> None:
    assert pronunciation.spoken(text) == said


def test_scan_finds_the_acronyms_of_a_real_training() -> None:
    found = {r["acronym"]: r for r in pronunciation.scan(BIG4)}
    assert {"AIA", "AIP", "PSQ", "PPVGA", "HTP"} <= set(found)
    assert found["AIA"]["spoken"] == "A I A" and found["AIA"]["count"] >= 2
    assert "AIA" in found["AIA"]["example"] and len(found["AIA"]["example"]) <= pronunciation.MAX_EXAMPLE
    counts = [r["count"] for r in pronunciation.scan(BIG4)]
    assert counts == sorted(counts, reverse=True)  # the most used first


def test_scan_only_looks_at_what_could_be_spoken() -> None:
    content: dict[str, Any] = {
        "training": {
            "title": "T",
            "trainer_name": "Anne",
            "lines": {"closing": "Thanks from NASA, {trainer_name}."},
        },
        "knowledge_base": {
            "preamble": [],
            "topics": [
                {
                    "number": 1,
                    "title": " x",
                    "lines": [
                        {"kind": "say", "prefix": "Say: ", "text": "Use the SIM tray."},
                        {"kind": "expected", "prefix": "Expected answer: ", "text": "They say XYZ."},
                        {"kind": "accept", "prefix": "Accept: ", "text": "Or QRS."},
                    ],
                }
            ],
        },
        "quiz": {
            "questions": [
                {"number": 3, "question": "What does ATM mean?", "options": {"A": "Cash", "B": "TLA"}},
                {"number": 4, "variants": {"fiber": {"question": "Is DSL slower?", "options": {}}}},
            ]
        },
    }
    found = {r["acronym"]: r["where"] for r in pronunciation.scan(content)}
    assert found == {
        "NASA": "Line closing",
        "SIM": "Topic 1",
        "ATM": "Quiz question 3",
        "TLA": "Quiz question 3",
        "DSL": "Quiz question 4",
    }


def test_scan_copes_with_an_empty_or_odd_document() -> None:
    assert pronunciation.scan({}) == []
    assert (
        pronunciation.scan({"training": None, "knowledge_base": {"topics": [{"lines": None}]}, "quiz": None})
        == []
    )


@pytest.fixture
def voice_setup(db_session: Session) -> None:
    seed(db_session)  # Matthew, big4 as version 1
    insert(db_session, "training_voices", voice_id="Ruth", display_name="Ruth", language_code="en-US",
           gender="Female", is_default=1, sort_order=10)  # fmt: skip


def check(client: TestClient, headers: dict[str, str], version_id: int, content: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/studio/pronunciation/check",
        headers=headers,
        json={"version_id": version_id, "content": content},
    )


def test_the_check_names_the_voice_and_lists_the_acronyms(
    client: TestClient, trainer_headers: dict[str, str], voice_setup: None
) -> None:
    r = check(client, trainer_headers, 1, BIG4)
    assert r.status_code == 200
    body = r.json()
    assert body["voice_id"] == "Ruth"  # no coach voice chosen: the default
    assert {a["acronym"] for a in body["acronyms"]} >= {"AIA", "PSQ"}
    # An unsaved coach voice in the editor is used.
    chosen = {**BIG4, "training": {**BIG4["training"], "voice": "Matthew"}}
    assert check(client, trainer_headers, 1, chosen).json()["voice_id"] == "Matthew"


def test_the_check_needs_a_real_version(
    client: TestClient, trainer_headers: dict[str, str], voice_setup: None
) -> None:
    assert check(client, trainer_headers, 999999, BIG4).status_code == 404


def test_a_sample_can_be_spoken_like_a_call(
    client: TestClient, trainer_headers: dict[str, str], voice_setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[str] = []
    monkeypatch.setattr(voices, "synthesize", lambda s, voice, engine, words: said.append(words) or MP3)
    url = "/api/v1/voices/Ruth/sample"
    client.post(url, headers=trainer_headers, json={"text": "Ask about PPVGA and AT&T."})
    client.post(url, headers=trainer_headers, json={"text": "Ask about PPVGA and AT&T.", "as_spoken": True})
    assert said == ["Ask about PPVGA and AT&T.", "Ask about P P V G A and AT&T."]
