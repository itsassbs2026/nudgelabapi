"""What can be exported, and how (SPEC §7.2 "Exports").

Every export calls the same function as the on-screen table, with the same filters, so the file always matches
the screen. Each report lists its columns (header, the key in the row, the kind of value) and the extra
parameters its table takes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.config import Settings
from app.reports import cost, drilldown, metrics, people, quality, sessions, trainings
from app.reports.filters import ReportFilters

Kind = Literal["text", "int", "float", "pct", "datetime", "date", "list"]


@dataclass(frozen=True)
class Col:
    header: str
    key: str  # dotted path into the row, e.g. "funnel.cohort"
    kind: Kind = "text"


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionParams(NoParams):
    uid: int | None = None
    outcome: Literal["passed", "not_passed", "no_quiz"] | None = None
    end_reason: Literal["completed", "user_left", "error", "dropped"] | None = None
    flagged: bool | None = None
    min_rating: int | None = Field(default=None, ge=1, le=10)
    max_rating: int | None = Field(default=None, ge=1, le=10)
    search: str | None = Field(default=None, max_length=100)


class FeedbackParams(NoParams):
    search: str | None = Field(default=None, max_length=100)
    min_rating: int | None = Field(default=None, ge=1, le=10)
    max_rating: int | None = Field(default=None, ge=1, le=10)


class AssignmentParams(NoParams):
    state: Literal["not_started", "in_progress", "completed", "overdue", "due_soon"] | None = None
    job_title: str | None = Field(default=None, max_length=100)
    search: str | None = Field(default=None, max_length=100)
    any_date: bool = False
    active_only: bool = False


class QualityParams(NoParams):
    status: Literal["open", "reviewed", "dismissed"] | None = None
    issue_type: str | None = Field(default=None, max_length=40)
    assignee_user_id: int | None = None


class QuestionParams(NoParams):
    training_id: str = Field(min_length=1, max_length=50)


class DrilldownParams(NoParams):
    level: Literal["region", "market", "district", "store", "employee"] = "region"
    parent: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _parent_is_valid(self) -> DrilldownParams:
        if self.level not in ("store", "employee") and self.parent is not None and not self.parent.isdigit():
            raise ValueError("parent must be a number for this level")
        return self


Fetch = Callable[[Session, Settings, ReportFilters, Any, int], list[dict[str, Any]]]


@dataclass(frozen=True)
class Report:
    name: str
    title: str
    columns: tuple[Col, ...]
    params: type[NoParams]
    fetch: Fetch


FUNNEL = (
    Col("Basis", "funnel.basis"),
    Col("Cohort", "funnel.cohort", "int"),
    Col("Started", "funnel.started", "int"),
    Col("Walkthrough done", "funnel.walkthrough_done", "int"),
    Col("Quiz attempted", "funnel.quiz_attempted", "int"),
    Col("Completed", "funnel.completed", "int"),
    Col("Completion rate", "funnel.completion_rate", "pct"),
)

REPORTS: dict[str, Report] = {
    r.name: r
    for r in (
        Report(
            "sessions",
            "Sessions",
            (
                Col("Session", "session_id"),
                Col("Started", "started_at", "datetime"),
                Col("Seconds", "duration_sec", "int"),
                Col("UID", "uid", "int"),
                Col("Trainee", "name"),
                Col("Store ID", "store_id"),
                Col("Store", "store_name"),
                Col("Training ID", "training_id"),
                Col("Training", "training_title"),
                Col("Outcome", "outcome"),
                Col("End reason", "end_reason"),
                Col("Client", "client"),
                Col("Rating", "rating", "int"),
                Col("Review score", "review_score", "int"),
                Col("Flagged", "flagged"),
                Col("Cost (USD)", "cost", "float"),
                Col("Recording", "recording"),
            ),
            SessionParams,
            lambda db, st, f, p, limit: sessions.session_list(
                db, st, f, **p.model_dump(), page=1, page_size=limit
            )["items"],
        ),
        Report(
            "trainings",
            "Trainings",
            (
                Col("Training ID", "training_id"),
                Col("Training", "title"),
                Col("Status", "status"),
                Col("Type", "completion_type"),
                Col("Active version", "active_version"),
                *FUNNEL,
                Col("Sessions", "sessions", "int"),
                Col("Trainees", "trainees", "int"),
                Col("Completions in period", "completions", "int"),
                Col("Average rating", "avg_rating", "float"),
                Col("Average review score", "avg_review_score", "float"),
                Col("Cost (USD)", "cost", "float"),
            ),
            NoParams,
            lambda db, st, f, p, limit: trainings.trainings_table(db, f),
        ),
        Report(
            "questions",
            "Quiz questions",
            (
                Col("Question", "question_number", "int"),
                Col("Section", "section_name"),
                Col("Text", "question"),
                Col("Correct option", "correct_option"),
                Col("Trainees", "trainees", "int"),
                Col("First-try accuracy", "first_try_accuracy", "pct"),
                Col("Answers", "answers", "int"),
                Col("Overall accuracy", "overall_accuracy", "pct"),
                Col("Most common wrong option", "most_common_wrong_option"),
                Col("Heard as", "heard_examples", "list"),
            ),
            QuestionParams,
            lambda db, st, f, p, limit: trainings.question_stats(db, f, p.training_id)["questions"],
        ),
        Report(
            "drilldown",
            "Drill-down",
            (
                Col("ID", "id"),
                Col("Name", "name"),
                Col("Cohort", "cohort", "int"),
                Col("Completed", "completed", "int"),
                Col("Completion rate", "completion_rate", "pct"),
                Col("Sessions", "sessions", "int"),
                Col("Active trainees", "trainees_active", "int"),
                Col("Average rating", "avg_rating", "float"),
                Col("Last activity", "last_activity", "datetime"),
            ),
            DrilldownParams,
            lambda db, st, f, p, limit: drilldown.drilldown(db, f, p.level, p.parent)["rows"],
        ),
        Report(
            "feedback",
            "Feedback",
            (
                Col("Session", "session_id"),
                Col("UID", "uid", "int"),
                Col("Trainee", "name"),
                Col("Store", "store_name"),
                Col("Training", "training_title"),
                Col("Rating", "rating", "int"),
                Col("Comment", "comment"),
                Col("Given", "created_at", "datetime"),
            ),
            FeedbackParams,
            lambda db, st, f, p, limit: people.feedback_list(
                db, f, **p.model_dump(), page=1, page_size=limit
            )["items"],
        ),
        Report(
            "acknowledgments",
            "Acknowledgments",
            (
                Col("UID", "uid", "int"),
                Col("Trainee", "name"),
                Col("Store", "store_name"),
                Col("Training", "training_title"),
                Col("Statement", "statement"),
                Col("Trainee's words", "trainee_quote"),
                Col("Acknowledged", "acknowledged_at", "datetime"),
                Col("Session", "session_id"),
            ),
            NoParams,
            lambda db, st, f, p, limit: people.acknowledgments_list(db, f, page=1, page_size=limit)["items"],
        ),
        Report(
            "assignments",
            "Assignments",
            (
                Col("UID", "uid", "int"),
                Col("Trainee", "name"),
                Col("Job title", "job_title"),
                Col("Store", "store_name"),
                Col("District", "district_name"),
                Col("Market", "market_name"),
                Col("Region", "region_name"),
                Col("Training", "training_title"),
                Col("Assigned", "assigned_at", "datetime"),
                Col("Assigned from", "assigned_via"),
                Col("Due", "due_at", "datetime"),
                Col("State", "state"),
                Col("Sessions", "sessions", "int"),
                Col("Started", "started_at", "datetime"),
                Col("Completed", "completed_at", "datetime"),
            ),
            AssignmentParams,
            lambda db, st, f, p, limit: people.assignments_list(
                db,
                f,
                state=p.state,
                page=1,
                page_size=limit,
                job_title=p.job_title,
                search=p.search,
                any_date=p.any_date,
                active_only=p.active_only,
            )["items"],  # fmt: skip
        ),
        Report(
            "quality",
            "Quality queue",
            (
                Col("Session", "session_id"),
                Col("Started", "started_at", "datetime"),
                Col("UID", "uid", "int"),
                Col("Trainee", "name"),
                Col("Training", "training_title"),
                Col("Review score", "score", "int"),
                Col("Summary", "summary"),
                Col("Issues", "issue_types", "list"),
                Col("Status", "status"),
                Col("Resolution", "resolution"),
                Col("Note", "note"),
                Col("Assignee", "assignee_name"),
                Col("Updated", "updated_at", "datetime"),
            ),
            QualityParams,
            lambda db, st, f, p, limit: quality.quality_list(
                db, f, **p.model_dump(), page=1, page_size=limit
            )["items"],
        ),
        Report(
            "daily",
            "Daily activity",
            (
                Col("Date", "date", "date"),
                Col("Sessions", "sessions", "int"),
                Col("Completions", "completions", "int"),
                Col("Cost (USD)", "cost", "float"),
            ),
            NoParams,
            lambda db, st, f, p, limit: metrics.daily_series(db, f),
        ),
        Report(
            "cost_by_training",
            "Cost by training",
            (
                Col("Training ID", "id"),
                Col("Training", "name"),
                Col("Sessions", "sessions", "int"),
                Col("Cost (USD)", "cost", "float"),
            ),
            NoParams,
            lambda db, st, f, p, limit: cost.cost_report(db, f)["by_training"],
        ),
    )
}


def value_at(row: dict[str, Any], key: str) -> Any:
    value: Any = row
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value
