"""The Flutter app's JSON (docs/APP_HANDOFF.md §3). Field names follow Wanaka's procedure, not this API's."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class SessionStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_over: bool = False


class SessionStartOut(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    expires_in: int
