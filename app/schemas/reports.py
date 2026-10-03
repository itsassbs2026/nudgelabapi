"""Response shapes for the report endpoints.

The dashboard generates its TypeScript types from these (SPEC §3.2).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Period(BaseModel):
    date_from: date
    date_to: date
    timezone: str


class ActivityOut(BaseModel):
    sessions: int
    trainees: int
    avg_session_seconds: float | None
    completions: int
    avg_rating: float | None
    ratings: int
    avg_review_score: float | None
    reviewed_sessions: int
    flagged_sessions: int
    cost: float
    cost_per_completion: float | None


class FunnelOut(BaseModel):
    basis: str  # "assigned" (Wanaka sync) or "started"
    cohort: int
    started: int
    walkthrough_done: int
    quiz_attempted: int
    completed: int
    completion_rate: float | None


class DailyPoint(BaseModel):
    date: date
    sessions: int
    completions: int
    cost: float


class TrainingCount(BaseModel):
    training_id: str
    title: str | None
    sessions: int
    completions: int


class OverviewOut(BaseModel):
    period: Period
    activity: ActivityOut
    funnel: FunnelOut
    daily: list[DailyPoint]
    by_training: list[TrainingCount]


class TrainingRow(BaseModel):
    training_id: str
    title: str | None
    status: str | None
    completion_type: str | None
    active_version: str | None
    funnel: FunnelOut
    sessions: int
    trainees: int
    completions: int
    avg_rating: float | None
    avg_review_score: float | None
    cost: float


class TrainingsOut(BaseModel):
    items: list[TrainingRow]


class DropoffStage(BaseModel):
    stage: str  # topic_N, quiz, acknowledgment, final_topic, not_started
    trainees: int


class TopicTiming(BaseModel):
    topic_number: int
    title: str | None
    median_seconds: int | None = None
    avg_seconds: int | None = None
    samples: int | None = None


class Comment(BaseModel):
    session_id: str
    uid: int
    name: str | None
    rating: int | None
    comment: str | None
    created_at: datetime | None


class RatingsOut(BaseModel):
    distribution: dict[str, int]
    count: int
    average: float | None
    comments: list[Comment]


class IssueCount(BaseModel):
    type: str
    count: int


class ReviewsOut(BaseModel):
    distribution: dict[str, int]
    average: float | None
    flagged: int
    top_issue_types: list[IssueCount]


class VersionRow(BaseModel):
    version_id: int
    label: str | None
    published_at: datetime | None
    is_active: bool
    sessions: int
    completions: int


class TrainingDetailOut(BaseModel):
    training_id: str
    title: str | None
    completion_type: str | None
    status: str | None
    active_version: str | None
    funnel: FunnelOut
    activity: ActivityOut
    dropoff: list[DropoffStage]
    topics: list[TopicTiming]
    ratings: RatingsOut
    reviews: ReviewsOut
    versions: list[VersionRow]


class QuestionStat(BaseModel):
    question_number: int
    section_code: str | None = None
    section_name: str | None = None
    question: str | None = None
    options: dict[str, str] | None = None
    correct_option: str | None = None
    trainees: int
    first_try_accuracy: float | None
    answers: int
    overall_accuracy: float | None
    most_common_wrong_option: str | None
    heard_examples: list[str]


class QuestionsOut(BaseModel):
    training_id: str
    title: str | None
    questions: list[QuestionStat]


class DrilldownRow(BaseModel):
    id: str
    name: str | None
    cohort: int
    completed: int
    completion_rate: float | None
    sessions: int
    trainees_active: int
    avg_rating: float | None
    last_activity: datetime | None


class DrilldownOut(BaseModel):
    level: str
    parent: str | None
    basis: str
    rows: list[DrilldownRow]


class StoreOut(BaseModel):
    store_id: str | None
    store_name: str | None
    district_id: int | None
    district_name: str | None
    market_id: int | None
    market_name: str | None
    region_id: int | None
    region_name: str | None


class AnswerOut(BaseModel):
    question_number: int
    round: int | None
    given_option: str | None
    is_correct: bool
    heard_as: str | None
    answered_at: datetime | None


class EmployeeTraining(BaseModel):
    training_id: str
    title: str | None
    completion_type: str | None
    status: str | None
    topics_covered: list[int]
    walkthrough_finished_at: datetime | None
    quiz_attempted: bool
    correct_questions: list[int]
    sessions_count: int | None
    first_started_at: datetime | None
    completed_at: datetime | None
    answers: list[AnswerOut]


class EmployeeSession(BaseModel):
    session_id: str
    training_id: str | None
    training_title: str | None
    started_at: datetime | None
    duration_sec: int | None
    start_point: str | None
    outcome: str | None
    end_reason: str | None
    client: str | None
    profile_id: str | None
    voice_id: str | None
    has_recording: bool
    cost: float | None
    rating: int | None
    review_score: int | None
    flagged: bool | None


class AcknowledgmentOut(BaseModel):
    training_id: str
    session_id: str
    statement: str
    trainee_quote: str
    acknowledged_at: datetime


class EmployeeOut(BaseModel):
    uid: int
    name: str | None
    is_active: bool | None
    job_title: str | None
    store: StoreOut | None
    trainings: list[EmployeeTraining]
    sessions: list[EmployeeSession]
    acknowledgments: list[AcknowledgmentOut]


class NamedCost(BaseModel):
    id: str | None
    name: str | None
    sessions: int
    cost: float | None


class DayCost(BaseModel):
    date: date
    cost: float


class CostSplit(BaseModel):
    claude: float
    polly: float
    transcribe: float


class CostOut(BaseModel):
    sessions: int
    total: float
    split: CostSplit
    per_session: float | None
    completions: int
    per_completion: float | None
    cache_hit_rate: float | None
    by_day: list[DayCost]
    by_training: list[NamedCost]
    by_setup: list[NamedCost]
    note: str


class FeedbackItem(BaseModel):
    session_id: str
    uid: int
    name: str | None
    store_name: str | None
    training_id: str | None
    training_title: str | None
    rating: int | None
    comment: str | None
    created_at: datetime | None


class FeedbackPage(BaseModel):
    items: list[FeedbackItem]
    total: int
    page: int
    page_size: int


class AcknowledgmentItem(BaseModel):
    ack_id: int
    uid: int
    name: str | None
    store_name: str | None
    training_id: str
    training_title: str | None
    statement: str
    trainee_quote: str
    acknowledged_at: datetime
    session_id: str


class AcknowledgmentPage(BaseModel):
    items: list[AcknowledgmentItem]
    total: int
    page: int
    page_size: int


class AssignmentItem(BaseModel):
    uid: int
    name: str | None
    store_name: str | None
    district_name: str | None
    training_id: str
    training_title: str | None
    assigned_at: datetime
    due_at: datetime | None
    state: str  # not_started | in_progress | completed | overdue
    sessions: int
    started_at: datetime | None
    completed_at: datetime | None


class AssignmentPage(BaseModel):
    counts: dict[str, int]
    items: list[AssignmentItem]
    total: int
    page: int
    page_size: int


class FilterOptions(BaseModel):
    trainings: list[dict[str, Any]]
    regions: list[dict[str, Any]]
    markets: list[dict[str, Any]]
    districts: list[dict[str, Any]]
    stores: list[dict[str, Any]]
    setups: list[dict[str, Any]]
    voices: list[dict[str, Any]]
    completion_types: list[str]


# -- sessions (Phase 4) --------------------------------------------------------------------------------------


class SessionItem(BaseModel):
    session_id: str
    started_at: datetime
    duration_sec: int | None
    uid: int
    name: str | None
    store_id: str | None
    store_name: str | None
    training_id: str
    training_title: str | None
    outcome: str | None
    end_reason: str | None
    client: str | None
    rating: int | None
    review_score: int | None
    flagged: bool
    cost: float | None
    recording: str  # available | none | expired


class SessionPage(BaseModel):
    items: list[SessionItem]
    total: int
    page: int
    page_size: int


class SessionStore(BaseModel):
    store_id: str | None
    store_name: str | None
    district_name: str | None
    market_name: str | None
    region_name: str | None


class SessionHeader(BaseModel):
    session_id: str
    uid: int
    trainee_name: str | None
    job_title_at_session: str | None
    training_id: str
    training_title: str | None
    completion_type: str | None
    version_id: int | None
    version_label: str | None
    store: SessionStore
    started_at: datetime
    ended_at: datetime | None
    duration_sec: int | None
    start_point: str | None
    outcome: str | None
    end_reason: str | None
    client: str | None
    profile_id: str | None
    profile_name: str | None
    voice_id: str | None
    voice_name: str | None
    llm_model: str | None
    agent_version: str | None
    summary: str | None
    recording: str


class TranscriptLine(BaseModel):
    seq: int
    role: str  # trainer | trainee
    message: str
    seconds: int
    interrupted: bool


class SessionEvent(BaseModel):
    # topic_reached | quiz_answer | guardrail | end_call_refused | error | dropped | reconnected |
    # not_reconnected | acknowledged | rating
    type: str
    at: datetime | None
    seconds: int | None
    label: str
    data: dict[str, Any]


class ReviewIssue(BaseModel):
    type: str | None
    time: str | None
    quote: str | None
    detail: str | None
    seq: int | None  # the transcript line the quote comes from
    seconds: int | None


class SessionReview(BaseModel):
    score: int | None
    summary: str | None
    flagged: bool
    reviewed_at: datetime | None
    model: str | None
    issues: list[ReviewIssue]


class SessionFeedback(BaseModel):
    rating: int | None
    comment: str | None
    trainee_quote: str | None


class SessionUsage(BaseModel):
    llm_model: str | None
    llm_requests: int | None
    llm_input_tokens: int | None
    llm_cached_tokens: int | None
    llm_cache_write_tokens: int | None
    llm_output_tokens: int | None
    tts_characters: int | None
    stt_audio_seconds: float | None
    recorded_seconds: int | None
    cost: dict[str, float | None]


class SessionDetail(BaseModel):
    session: SessionHeader
    transcript: list[TranscriptLine]
    events: list[SessionEvent]
    review: SessionReview | None
    feedback: SessionFeedback | None
    usage: SessionUsage | None
    quality: QualityState | None = None  # set when the AI review flagged the session


class RecordingUrl(BaseModel):
    url: str
    content_type: str
    expires_at: datetime


# -- quality & exports (Phase 5) -----------------------------------------------------------------------------


class QualityItem(BaseModel):
    session_id: str
    started_at: datetime
    uid: int
    name: str | None
    training_id: str
    training_title: str | None
    score: int | None
    summary: str | None
    issue_types: list[str]
    issue_count: int
    status: str  # open | reviewed | dismissed
    resolution: str | None
    note: str | None
    assignee_user_id: int | None
    assignee_name: str | None
    updated_at: datetime | None
    updated_by_name: str | None


class QualityPage(BaseModel):
    counts: dict[str, int]
    items: list[QualityItem]
    total: int
    page: int
    page_size: int


class QualityState(BaseModel):
    status: str
    resolution: str | None
    note: str | None
    assignee_user_id: int | None
    updated_at: datetime | None


class QualityUpdate(BaseModel):
    """Only the fields sent are changed; send null to clear resolution, note or assignee."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "reviewed", "dismissed"] | None = None
    resolution: Literal["script_changed", "agent_issue", "no_action", "other"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    assignee_user_id: int | None = None


class ExportJob(BaseModel):
    id: int
    status: str  # queued | running | done | failed
    report: str | None
    format: str | None
    rows: int | None
    filename: str | None
    expired: bool
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class SearchEmployee(BaseModel):
    uid: int
    name: str | None
    store_name: str | None
    is_active: bool


class SearchSession(BaseModel):
    session_id: str
    started_at: datetime
    uid: int
    name: str | None
    training_title: str | None


class SearchTraining(BaseModel):
    training_id: str
    title: str | None


class SearchStore(BaseModel):
    store_id: str
    store_name: str | None
    district_id: int | None


class SearchOut(BaseModel):
    employees: list[SearchEmployee]
    sessions: list[SearchSession]
    trainings: list[SearchTraining]
    stores: list[SearchStore]


SessionDetail.model_rebuild()  # QualityState is defined after it
