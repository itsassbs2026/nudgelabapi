"""Publish checks (SPEC 10.4, app/studio/validate.py) and how saves treat nulls."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from app.studio.blank import blank_content
from app.studio.validate import validate
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import seed

FIXTURES = Path(__file__).parent / "fixtures" / "content"
LIVE = ["big4", "q4_comp_2026", "sample_store_safety", "sample_return_policy"]


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", LIVE)
def test_live_trainings_have_no_errors(name: str) -> None:
    """They run in production today, so any error here would be a false alarm."""
    assert validate(load(name), completion_key="agent_x")["errors"] == []


def messages(result: dict[str, list[dict[str, Any]]], kind: str = "errors") -> set[tuple[str, str]]:
    return {(issue["where"], issue["message"]) for issue in result[kind]}


def walkthrough() -> dict[str, Any]:
    content = blank_content(
        title="T", completion_type="walkthrough", uses_location=False, trainer_name="Anne"
    )
    for key in content["training"]["lines"]:
        content["training"]["lines"][key] = f"The {key} line."
    content["training"]["lines"]["first_message"] = "Hi! I'm {trainer_name}. Ready?"
    content["knowledge_base"]["topics"] = [
        {
            "number": n,
            "title": f" Topic {n}",
            "lines": [
                {"kind": "say", "prefix": "Say: ", "text": f"Teaching point {n}."},
                {"kind": "ask", "prefix": "Ask: ", "text": f"What is teaching point number {n}?"},
                {"kind": "expected", "prefix": "Expected: ", "text": f"Point {n}."},
                {"kind": "accept", "prefix": "Accept: ", "text": f"{n}"},
            ],
        }
        for n in (1, 2, 3)
    ]
    return content


def test_a_complete_walkthrough_passes() -> None:
    assert validate(walkthrough(), completion_key="agent_x") == {"errors": [], "warnings": []}


def test_blank_training_lists_everything_to_do() -> None:
    blank = blank_content(
        title="T", completion_type="acknowledgment", uses_location=True, trainer_name="Anne"
    )
    found = messages(validate(blank, completion_key=None))
    assert ("Topics", "Add at least one topic.") in found
    assert ("Acknowledgment", "Write the statement the trainee confirms (10–300 characters).") in found
    for key in ("first_message", "completed", "acknowledgment_intro", "location_unknown"):
        assert (f"Line {key}", "Write this line.") in found
    assert ("Settings", "No completion key: passes won't be copied to Prime Portal.") in messages(
        validate(blank, completion_key=None), "warnings"
    )


def test_topic_rules() -> None:
    content = walkthrough()
    topics = content["knowledge_base"]["topics"]
    topics[0]["lines"][1]["text"] = "Any questions?"  # three words
    topics[2]["lines"][1]["text"] = topics[1]["lines"][1]["text"].upper()  # same question, different case
    del topics[1]["lines"][3]  # no Accept
    topics[1]["lines"].insert(0, {"kind": "say", "prefix": "Say: ", "text": "x" * 500})  # too long
    result = validate(content, completion_key="agent_x")
    assert messages(result) == {
        ("Topic 1", "The question needs at least 4 words."),
        ("Topic 3", "Same question as topic 2; each question must differ."),
    }
    warned = messages(result, "warnings")
    assert ("Topic 2", "No Accept line: only the Expected answer counts.") in warned
    assert any(where == "Topic 2" and "seconds of speech" in message for where, message in warned)
    assert all(issue.get("topic") for issue in result["errors"])  # the editor can point at them


def test_quiz_rules() -> None:
    content = load("big4")
    quiz = content["quiz"]
    quiz["sections"]["A"]["topics"] = [1, 99]
    quiz["questions"][0]["options"]["A"] = " "  # B and C left: not a valid set
    quiz["questions"][1]["correct"] = "D"
    quiz["questions"][2]["explanation"] = ""
    quiz["questions"][3]["section"] = "Z"
    found = messages(validate(content, completion_key="agent_x"))
    assert found == {
        ("Quiz section A", "Refers to topics that don't exist: [99]."),
        ("Quiz question 1", "Write the options: A and B, and C if there are three."),
        ("Quiz question 2", "Choose the correct answer."),
        ("Quiz question 3", "Write a short explanation."),
        ("Quiz question 4", "Put the question in a section."),
    }
    content["quiz"]["questions"] = []
    assert ("Quiz", "Add at least one question.") in messages(validate(content, completion_key="agent_x"))


def test_trainer_name_warnings() -> None:
    content = walkthrough()
    content["training"]["lines"]["first_message"] = "Hi, I'm Anne!"
    content["training"]["lines"]["closing"] = "Anne says bye."
    warned = messages(validate(content, completion_key="agent_x"), "warnings")
    assert ("Line first_message", "Use {trainer_name} so each trainee hears their trainer's name.") in warned
    assert ("Line closing", 'Says "Anne"; use {trainer_name} instead.') in warned


def test_location_variants_are_checked() -> None:
    content = load("q4_comp_2026")
    question = next(q for q in content["quiz"]["questions"] if q.get("variants"))
    first = next(iter(question["variants"]))
    question["variants"][first]["correct"] = "X"
    assert (f"Quiz question {question['number']}", "Choose the correct answer.") in messages(
        validate(content, completion_key="agent_x")
    )


# --- Endpoint and saves ----------------------------------------------------------------------------------


@pytest.fixture
def big4_live(db_session: Session) -> None:
    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(load("big4"))},
    )


def test_validate_endpoint(client: TestClient, trainer_headers: dict[str, str], big4_live: None) -> None:
    body = client.post("/api/v1/versions/1/validate", headers=trainer_headers).json()
    assert body["errors"] == []
    assert any(w["where"] == "Settings" for w in body["warnings"])  # seed's big4 has no completion key
    assert (
        client.post("/api/v1/versions/2/validate", headers=trainer_headers).status_code == 422
    )  # no content
    assert client.post("/api/v1/versions/999/validate", headers=trainer_headers).status_code == 404


def test_saves_drop_nulls_the_agent_would_misread(
    client: TestClient, trainer_headers: dict[str, str], big4_live: None
) -> None:
    draft = client.post(
        "/api/v1/trainings/big4/versions",
        headers=trainer_headers,
        json={"source": "version", "source_version_id": 1},
    ).json()
    content = copy.deepcopy(load("big4"))
    content["training"]["completion_type"] = None  # the agent reads a null type as an error
    content["training"]["stt_vocabulary"] = None
    content["knowledge_base"]["topics"][0]["lines"][0]["locations"] = None
    content["vocabulary"] = None  # stays: null means "none"
    url = f"/api/v1/versions/{draft['version_id']}/content"
    assert (
        client.put(url, headers=trainer_headers, json={"revision": 1, "content": content}).status_code == 200
    )
    saved = client.get(url, headers=trainer_headers).json()["content"]
    assert "completion_type" not in saved["training"] and "stt_vocabulary" not in saved["training"]
    assert "locations" not in saved["knowledge_base"]["topics"][0]["lines"][0]
    assert "vocabulary" in saved and saved["vocabulary"] is None


# -- Role Play (docs/ROLEPLAY.md) ----------------------------------------------------------------------------


def test_win_every_customer_has_no_errors() -> None:
    result = validate(load("win_every_customer"), completion_key="agent_x")
    assert result["errors"] == []
    assert (
        "Track General, quiz",
        "Marked as a draft: Rewritten from open-answer questions to A–D on 2026-10-07; "
        "needs review by the content owner.",
    ) in messages(result, "warnings")


def test_a_blank_roleplay_lists_everything_to_fill_in() -> None:
    content = blank_content(title="T", completion_type="roleplay", uses_location=False, trainer_name="Jordan")
    found = messages(validate(content, completion_key="agent_x"))
    assert ("Scoring", "Describe what a 4 means.") in found
    assert ("Track General", "Add at least one coaching item.") in found
    assert ("Track General, Beginner customer", "Write the customer's opening line.") in found
    assert ("Track General, quiz", "Add at least one question.") in found
    assert ("Line closing_not_passed", "Write this line.") in found
    assert not any(where == "Topics" for where, _ in found)  # no walkthrough topics in a roleplay


def test_roleplay_mistakes_are_caught() -> None:
    content = load("win_every_customer")
    rp = content["roleplay"]
    billing = next(t for t in rp["tracks"] if t["id"] == "billing")
    billing["quiz"]["questions"][0]["options"] = {"A": "x", "C": "y"}
    billing["quiz"]["questions"][1]["section"] = "nowhere"
    billing["reasons"] = ["wait"]  # also picks the Wait track
    billing["coach_items"][0]["probe"] = "Why?"
    rp["default_track"] = "missing"
    rp["settings"]["unlock_score"] = 9
    found = messages(validate(content, completion_key="agent_x"))
    assert (
        "Track Billing, quiz question 1",
        "Write the options in order: A and B, then C and D if there are more.",
    ) in found
    assert ("Track Billing, quiz question 2", "Put the question in a section.") in found
    assert ("Track Billing", 'The reason "wait" also picks track Wait.') in found or (
        "Track Wait",
        'The reason "wait" also picks track Billing.',
    ) in found
    assert (
        'Track Billing, coaching "Billing discrepancies"',
        "The question needs at least 4 words.",
    ) in found
    assert (
        "Tracks",
        "Choose the default track (used when a person's reason is missing or unknown).",
    ) in found
    assert ("Settings", "The score that opens the quiz must be 1 to 5.") in found


def test_a_coach_voice_must_be_switched_on(
    client: TestClient, trainer_headers: dict[str, str], big4_live: None, db_session: Session
) -> None:
    """The studio's Coach voice (training.voice) must be an active voice, or it can't be published."""
    content = load("big4")

    def check(voice: str | None) -> list[dict[str, Any]]:
        content["training"]["voice"] = voice
        db_session.connection().execute(
            text("UPDATE training_versions SET content = :c WHERE version_id = 1"), {"c": json.dumps(content)}
        )
        return client.post("/api/v1/versions/1/validate", headers=trainer_headers).json()["errors"]

    assert check("Matthew") == [] and check(None) == []  # an active voice, or the setup's
    (error,) = check("Joanna")  # not in training_voices
    assert error["where"] == "Settings" and "Joanna" in error["message"]
