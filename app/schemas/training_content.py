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
Letter = Literal["A", "B", "C", "D"]  # D: Role Play quizzes have four options

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
CompletionType = Literal["quiz", "walkthrough", "acknowledgment", "roleplay"]


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
    topics: list[int] | None = None  # quiz trainings: the knowledge base topics it covers
    items: list[str] | None = None  # roleplay tracks: the coaching items it covers


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


# -- Role Play (docs/ROLEPLAY.md; the agent's roleplay.py reads this) ----------------------------------------


class CoachItem(Strict):
    id: str
    title: str
    probe: str  # the question the coach asks, exactly as written
    right_answer: str  # what a good answer contains (never read aloud)
    teach: str = ""  # what to teach when they miss it
    last: bool | None = None  # shared items only: coached after the track's own items


class Coach(Strict):
    manner: str = ""
    purpose: str = ""
    items: list[CoachItem] = Field(default_factory=list)  # shared by every track
    closing_line: str = ""
    notes: list[str] = Field(default_factory=list)


class Branch(Strict):
    # "if" is a Python keyword: read and written as "if", the agent's key.
    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    if_: str = Field(alias="if")  # what the trainee does
    then: str = ""  # how the customer reacts
    quick_pause: str | None = None  # the step the coach names when stepping in (Beginner only)


class Persona(Strict):
    name: str
    who: str
    opening: str
    setup: str = ""
    branches: list[Branch] = Field(default_factory=list)
    close: str = ""
    test: str = ""
    debrief_must_cover: str = ""
    voice: str | None = None


class Framework(Strict):
    name: str
    steps: list[str]


class TrackQuiz(Strict):
    draft: str | None = None
    sections: dict[str, Section]
    questions: list[Question]


class Track(Strict):
    id: str
    name: str
    reasons: list[str] = Field(default_factory=list)
    why_assigned: str = ""
    framework: Framework
    coach_items: list[CoachItem] = Field(default_factory=list)
    beginner: Persona
    stress: Persona | None = None
    quiz: TrackQuiz


class Practice(Strict):
    scene_rules: list[str] = Field(default_factory=list)
    rubric: dict[Literal["1", "2", "3", "4", "5"], str]
    score_lines: dict[Literal["1", "2", "3", "4", "5"], str] = Field(default_factory=dict)
    meter_name: str = "Win Meter"


class RoleplaySettings(Strict):
    unlock_score: int = Field(default=4, ge=1, le=5)
    quiz_pass_count: int | None = Field(default=None, ge=1)  # None: every question right
    stress_enabled: bool = True
    quick_pauses_in_beginner: bool = True
    scene_exchanges: list[int] = Field(default_factory=lambda: [6, 12])
    customer_voice: str | None = None


class RoleplayContent(Strict):
    coach: Coach
    practice: Practice
    settings: RoleplaySettings
    default_track: str
    tracks: list[Track]


class TrainingContent(Strict):
    format: Literal[1]
    training: TrainingSettings
    knowledge_base: KnowledgeBase
    quiz: Quiz | None
    vocabulary: list[str] | None
    roleplay: RoleplayContent | None = None  # only for Role Play trainings
