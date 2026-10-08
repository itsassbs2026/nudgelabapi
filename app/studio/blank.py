"""A new training's first draft: the content document's structure with nothing written yet.

The line keys are the ones the agent reads for each completion type (the agent's training.py and its README's
"Completion types" table). They start empty: the trainer writes them, and validation (SPEC 10.4) won't let a
version with an empty required line be published. Nothing here invents wording.
"""

from __future__ import annotations

from typing import Any

from app.schemas.training_content import CompletionType

DEFAULT_COMPANY = "Prime Communications"

_COMMON = ("first_message", "already_passed", "feedback_question", "closing", "welcome_back_walkthrough")
REQUIRED_LINES: dict[str, tuple[str, ...]] = {
    "quiz": (
        *_COMMON,
        "quiz_intro",
        "perfect_score",
        "passed_after_retry",
        "welcome_back_quiz",
        "welcome_back_retry",
    ),
    "walkthrough": (*_COMMON, "completed"),
    "acknowledgment": (*_COMMON, "completed", "acknowledgment_intro", "welcome_back_acknowledgment"),
    # Role Play (docs/ROLEPLAY.md): coaching, practice and the quiz; no walkthrough topics.
    "roleplay": (
        "first_message",
        "already_passed",
        "feedback_question",
        "closing",
        "closing_not_passed",
        "welcome_back",
        "quiz_intro",
        "perfect_score",
        "passed_after_retry",
    ),
}
LOCATION_LINES = ("location_unknown",)


def required_lines(completion_type: CompletionType, uses_location: bool) -> tuple[str, ...]:
    return REQUIRED_LINES[completion_type] + (LOCATION_LINES if uses_location else ())


def blank_content(
    *, title: str, completion_type: CompletionType, uses_location: bool, trainer_name: str
) -> dict[str, Any]:
    training: dict[str, Any] = {
        "title": title,
        "trainer_name": trainer_name,
        "company": DEFAULT_COMPANY,
        "completion_type": completion_type,
        "uses_location": uses_location,
        "lines": dict.fromkeys(required_lines(completion_type, uses_location), ""),
    }
    if completion_type == "acknowledgment":
        training["acknowledgment"] = ""
    document: dict[str, Any] = {
        "format": 1,
        "training": training,
        "knowledge_base": {"preamble": [], "topics": [], "ends_with_newline": True},
        "quiz": {"sections": {}, "questions": []} if completion_type == "quiz" else None,
        "vocabulary": None,
    }
    if completion_type == "roleplay":
        document["roleplay"] = blank_roleplay()
    return document


def blank_roleplay() -> dict[str, Any]:
    """The Role Play template: one track, nothing written (the studio fills it in; nothing is invented)."""
    persona = {"name": "", "who": "", "opening": "", "setup": "", "branches": [], "close": ""}
    return {
        "coach": {"manner": "", "purpose": "", "items": [], "closing_line": "", "notes": []},
        "practice": {
            "scene_rules": [],
            "rubric": dict.fromkeys("12345", ""),
            "score_lines": dict.fromkeys("12345", ""),
        },
        "settings": {
            "unlock_score": 4,
            "quiz_pass_count": None,
            "stress_enabled": True,
            "quick_pauses_in_beginner": True,
            "scene_exchanges": [6, 12],
            "customer_voice": None,
        },
        "default_track": "general",
        "tracks": [
            {
                "id": "general",
                "name": "General",
                "reasons": [],
                "why_assigned": "",
                "framework": {"name": "", "steps": []},
                "coach_items": [],
                "beginner": dict(persona),
                "stress": None,
                "quiz": {"sections": {}, "questions": []},
            }
        ],
    }
