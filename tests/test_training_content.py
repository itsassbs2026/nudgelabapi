"""The content schema matches what the voice agent writes (SPEC §6.5): every exported training validates and
comes back unchanged. Fixtures: `uv run content.py export <training>` in the agent repo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.schemas.training_content import TrainingContent
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "content"
TRAININGS = sorted(p.stem for p in FIXTURES.glob("*.json"))


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_every_training_is_covered() -> None:
    assert TRAININGS == [
        "big4", "q4_comp_2026", "sample_return_policy", "sample_store_safety", "win_every_customer"
    ]  # fmt: skip


@pytest.mark.parametrize("name", TRAININGS)
def test_agent_export_validates_and_round_trips(name: str) -> None:
    raw = load(name)
    content = TrainingContent.model_validate(raw)
    assert content.model_dump(mode="json", exclude_unset=True, by_alias=True) == raw


def test_quiz_variants_and_completion_types() -> None:
    q4 = TrainingContent.model_validate(load("q4_comp_2026"))
    assert q4.quiz is not None and any(q.variants for q in q4.quiz.questions)
    ack = TrainingContent.model_validate(load("sample_return_policy"))
    assert ack.training.completion_type == "acknowledgment" and ack.training.acknowledgment
    assert ack.quiz is None


def test_roleplay_content() -> None:
    """Role Play (docs/ROLEPLAY.md): tracks, personas (a branch's "if" key), four-option quizzes."""
    rp = TrainingContent.model_validate(load("win_every_customer"))
    assert rp.training.completion_type == "roleplay" and rp.roleplay is not None
    billing = next(t for t in rp.roleplay.tracks if t.id == "billing")
    assert billing.beginner.branches[0].if_.startswith("The trainee understands the concern first")
    assert set(billing.quiz.questions[0].options or {}) == {"A", "B", "C", "D"}
    assert rp.roleplay.settings.unlock_score == 4 and rp.roleplay.default_track == "general"


def test_bad_content_is_refused() -> None:
    raw = load("big4")
    raw["knowledge_base"]["topics"][0]["lines"][0]["kind"] = "shout"
    with pytest.raises(ValidationError):
        TrainingContent.model_validate(raw)
    raw = load("big4")
    raw["format"] = 2
    with pytest.raises(ValidationError):
        TrainingContent.model_validate(raw)
