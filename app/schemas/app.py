"""The Flutter app's JSON (docs/APP_HANDOFF.md §3). Field names follow Wanaka's procedure, not this API's."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrainingProgress(BaseModel):
    topics_done: int
    topics_total: int
    quiz_retry: bool


class TrainingCard(BaseModel):
    tags: str | None
    trainer_id: int | None
    assigned_at: str | None
    trainer_key: str
    trainer_name: str
    trainer_category: str | None
    elevenlabs_agent_id: str | None
    trainer_description: str | None
    trainer_person_name: str | None
    trainer_picture: str | None
    matched_rule_group_id: int | None
    matched_rule_name: str | None
    ai_flag: str | None
    is_required: bool
    is_completed: bool
    training_id: str
    completion_type: Literal["quiz", "walkthrough", "acknowledgment"]
    status: Literal["not_started", "in_progress", "completed"]
    progress: TrainingProgress
    due_at: str | None
    due_date: str | None  # yyyy-mm-dd, the due day in the company's time zone (2026-10-21)
    is_overdue: bool  # due date passed and not completed
    trainer_voice: str | None
    default_trainer_name: str


class TrainingList(BaseModel):
    uid: int
    name: str | None
    job_id: int | None
    store_id: str | None
    job_title: str | None
    store_name: str | None
    market_name: str | None
    region_name: str | None
    district_name: str | None
    assignment_month: str
    assigned_trainers: list[TrainingCard]


class PendingCount(BaseModel):
    uid: int
    name: str | None
    incompleted_count: int
    overdue_count: int  # listed trainings past their due date and not completed (2026-10-21)


class SessionStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_over: bool = False
    # Send back what the list offered: trainer_person_name (or default_trainer_name) and trainer_voice.
    trainer_name: str | None = Field(default=None, min_length=1, max_length=120)
    trainer_voice: str | None = Field(default=None, min_length=1, max_length=40)


class HandoffIn(BaseModel):
    """The portal asks for a Start link (app/mobile/handoff.py)."""

    model_config = ConfigDict(extra="forbid")

    training_id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    start_over: bool = False
    return_url: str | None = Field(default=None, max_length=500)  # kept only if it is on a portal address


class HandoffOut(BaseModel):
    code: str
    url: str  # the training page, with the code
    expires_in: int


class RedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=10, max_length=100)


class SessionStartOut(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    expires_in: int
    trainer_name: str | None  # the name the trainer will say (first name)
    trainer_voice: str | None


class RedeemOut(SessionStartOut):
    """The training page's session: as the app's, plus what the page shows around it."""

    training_id: str
    training_title: str
    return_url: str | None
    first_name: str | None
