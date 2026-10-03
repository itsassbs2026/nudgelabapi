"""The training content document (SPEC §6.5): `training_versions.content`, shared with the voice agent.

The agent's `content.py` writes and reads this format; this is the API's copy of the contract. It's checked
against real exports of every training (tests/fixtures/content, made with `uv run content.py export <id>` in
the agent repo): each validates, and dumps back unchanged. Regenerate the fixtures when the agent's format
changes.

Format 1 mirrors the training's files losslessly: `training` is training.json as written, the knowledge base
is split into topics and typed lines, `quiz` is quiz.json, `vocabulary` is vocabulary.txt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Location = Literal["fiber", "aia_only", "alaska_only"]
Letter = Literal["A", "B", "C"]

# What a knowledge-base line is for (the agent's content.LINE_KINDS, plus "text" for anything else).
LineKind = Literal[
    "say",
    "say_exactly",
    "ask",
    "expected",
    "accept",
    "key_point",
    "key_point_exactly",
    "then_say",
    "then_add",
    "if_missed",
    "if_right",
    "first_say",
    "text",
]
CompletionType = Literal["quiz", "walkthrough", "acknowledgment"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KbLine(Strict):
    kind: LineKind
    prefix: str  # the marker exactly as written, e.g. "Expected answer: "; "" for plain text
    text: str
    locations: list[Location] | None = None  # only for location-specific lines


class Topic(Strict):
    number: int = Field(ge=1)
    title: str  # as written after "TOPIC n:", including its leading space
    lines: list[KbLine]


class KnowledgeBase(Strict):
    preamble: list[KbLine]
    topics: list[Topic]
    ends_with_newline: bool


class QuestionBody(Strict):
    question: str
    options: dict[Letter, str]
    correct: Letter
    explanation: str


class Question(Strict):
    number: int = Field(ge=1)
    section: str
    # Either one body for everyone, or one per location ("variants").
    question: str | None = None
    options: dict[Letter, str] | None = None
    correct: Letter | None = None
    explanation: str | None = None
    variants: dict[Location, QuestionBody] | None = None


class Section(Strict):
    name: str
    topics: list[int]


class Quiz(Strict):
    sections: dict[str, Section]
    questions: list[Question]


class OpeningStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    say: str | None = None
    ask: str | None = None
    otherwise: str | None = None


class TrainingSettings(BaseModel):
    """training.json. Extra keys are kept (the agent stores the file as written)."""

    model_config = ConfigDict(extra="allow")

    title: str
    trainer_name: str
    company: str
    completion_type: CompletionType | None = None  # missing means "quiz"
    acknowledgment: str | None = None
    uses_location: bool | None = None
    stt_vocabulary: str | None = None
    opening: list[OpeningStep] | None = None
    lines: dict[str, str]


class TrainingContent(Strict):
    format: Literal[1]
    training: TrainingSettings
    knowledge_base: KnowledgeBase
    quiz: Quiz | None
    vocabulary: list[str] | None
