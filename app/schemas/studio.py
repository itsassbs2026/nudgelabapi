"""Training studio requests and responses (SPEC 8.3, Phase 11)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.training_content import CompletionType, TrainingContent

# A training id becomes part of LiveKit room names (`nl-<training>-<uid>-<tag>`) and file names on the agent
# server: lowercase letters, digits and underscores only, starting with a letter.
TRAINING_ID_PATTERN = r"^[a-z][a-z0-9_]{2,49}$"
COMPLETION_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class TrainingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: str = Field(pattern=TRAINING_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    completion_type: CompletionType = "quiz"
    uses_location: bool = False
    trainer_name: str = Field(default="Anne", min_length=1, max_length=40)
    profile_id: str | None = Field(default=None, max_length=30)
    completion_key: str | None = Field(default=None, pattern=COMPLETION_KEY_PATTERN)

    @field_validator("title", "trainer_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Can't be blank.")
        return value.strip()


class TrainingUpdate(BaseModel):
    """Only the fields sent are changed. `archived` and `app_status` are Admin-only."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    completion_type: CompletionType | None = None
    uses_location: bool | None = None
    completion_key: str | None = Field(default=None, pattern=COMPLETION_KEY_PATTERN)
    profile_id: str | None = Field(default=None, max_length=30)
    app_title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    tags: str | None = Field(default=None, max_length=500)
    is_required: bool | None = None
    app_status: Literal["active", "archived"] | None = None
    archived: bool | None = None

    @field_validator("app_title", "category", "description", "tags")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _strip(value)

    @model_validator(mode="after")
    def _not_null(self) -> TrainingUpdate:
        # These can be changed but not cleared; the others can be cleared with null.
        for name in ("title", "completion_type", "uses_location", "is_required", "app_status", "archived"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be null.")
        if "title" in self.model_fields_set and self.title is not None and not self.title.strip():
            raise ValueError("title can't be blank.")
        return self

    def changes(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.model_fields_set}


class VersionRef(BaseModel):
    version_id: int
    label: str


class TrainingSettings(BaseModel):
    training_id: str
    title: str
    status: str
    completion_type: CompletionType
    uses_location: bool
    completion_key: str | None
    profile_id: str | None
    app_title: str | None
    category: str | None
    description: str | None
    tags: str | None
    is_required: bool
    app_status: str
    wanaka_trainer_id: int | None


class TrainingListItem(TrainingSettings):
    active_version: VersionRef | None
    drafts: int
    in_review: int
    last_edited_at: datetime | None


class VersionSummary(BaseModel):
    version_id: int
    training_id: str
    label: str
    status: Literal["draft", "in_review", "published", "retired"]
    is_active: bool
    revision: int
    has_content: bool
    notes: str | None
    created_at: datetime | None
    created_by: str | None
    updated_at: datetime | None
    updated_by: str | None
    published_at: datetime | None
    source_upload_id: int | None


class TrainingDetail(TrainingSettings):
    active_version_id: int | None
    versions: list[VersionSummary]


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["blank", "version", "upload"] = "version"
    source_version_id: int | None = None
    upload_id: int | None = None  # source "upload": a ready document; the draft is then prepared for voice
    label: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _source(self) -> VersionCreate:
        if self.source == "version" and self.source_version_id is None:
            raise ValueError("source_version_id is required to copy a version.")
        if self.source != "version" and self.source_version_id is not None:
            raise ValueError("source_version_id is only for copying a version.")
        if (self.source == "upload") != (self.upload_id is not None):
            raise ValueError("upload_id is required for, and only for, a draft made from a document.")
        self.label = _strip(self.label)
        self.notes = _strip(self.notes)
        return self


class VersionContent(VersionSummary):
    content: dict[str, Any] | None


class ContentSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    content: TrainingContent


class VersionDiff(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: VersionSummary = Field(alias="from")
    to: VersionSummary
    identical: bool
    settings: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    preamble: list[dict[str, Any]]
    topics: list[dict[str, Any]]
    quiz: dict[str, Any]
    vocabulary: dict[str, list[str]]


class UploadPresignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf",
        "text/plain",
        "text/markdown",
    ]
    size_bytes: int = Field(ge=1)

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        """Kept only to show people; never used in a storage key. Any folder part is dropped."""
        value = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Not a valid file name.")
        return value


class UploadPresignOut(BaseModel):
    upload_id: int
    url: str
    fields: dict[str, str]
    expires_in: int
    max_bytes: int


class UploadSummary(BaseModel):
    upload_id: int
    training_id: str
    filename: str
    content_type: str
    size_bytes: int | None
    status: Literal["pending", "processing", "ready", "rejected"]
    error: str | None
    text_chars: int | None
    uploaded_by: str | None
    created_at: datetime
    completed_at: datetime | None


class UploadText(BaseModel):
    upload_id: int
    filename: str
    text: str


class JobView(BaseModel):
    job_id: int
    type: str
    status: Literal["queued", "running", "done", "failed"]
    training_id: str | None
    version_id: int | None
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
