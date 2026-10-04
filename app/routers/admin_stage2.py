"""Voices, setups and testers (SPEC 10.3, Phase 14). Admin only."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, require_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.routers.auth import client_ip
from app.routers.voices import VoiceOut
from app.services.voices import list_voices
from app.studio import admin
from app.studio.schemas_common import TRAINING_IDS
from app.utils.errors import ApiError

router = APIRouter(prefix="/admin", tags=["admin"])

VoiceId = Path(min_length=1, max_length=40, pattern=r"^[A-Za-z-]+$")
ProfileId = Path(min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")


def _not_null(model: BaseModel, names: tuple[str, ...]) -> None:
    for name in names:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} can't be empty.")


# --- Voices ----------------------------------------------------------------------------------------------


class AdminVoiceOut(VoiceOut):
    sort_order: int


class VoiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def _check(self) -> VoiceUpdate:
        _not_null(self, ("is_active", "sort_order"))
        return self


class VoiceAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z-]+$")


class PollyVoice(BaseModel):
    voice_id: str
    name: str
    gender: str
    language_code: str
    language_name: str


@router.get("/voices", response_model=list[AdminVoiceOut])
def voices(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return list_voices(db)


@router.patch("/voices/{voice_id}", response_model=list[AdminVoiceOut])
def update_voice(
    body: VoiceUpdate,
    request: Request,
    voice_id: str = VoiceId,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    return admin.update_voice(db, current, voice_id, changes, client_ip(request))


@router.post("/voices/{voice_id}/default", response_model=list[AdminVoiceOut])
def default_voice(
    request: Request,
    voice_id: str = VoiceId,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return admin.make_default_voice(db, current, voice_id, client_ip(request))


@router.get("/voices/available", response_model=list[PollyVoice])
def available_voices(
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Polly's generative English voices that aren't in the list yet."""
    return admin.available_voices(db, settings)


@router.post("/voices", response_model=list[AdminVoiceOut])
def add_voice(
    body: VoiceAdd,
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return admin.add_voice(db, settings, current, body.voice_id, client_ip(request))


# --- Setups ----------------------------------------------------------------------------------------------

Price = Decimal


class ProfileOut(BaseModel):
    profile_id: str
    display_name: str
    description: str | None
    llm_model: str
    llm_model_label: str
    llm_model_known: bool
    llm_effort: str | None
    llm_max_output_tokens: int
    tts_engine: str
    voice_id: str | None
    llm_input_per_m: float
    llm_cached_per_m: float
    llm_cache_write_per_m: float
    llm_output_per_m: float
    tts_per_m_chars: float
    stt_per_minute: float
    is_default: bool
    is_active: bool
    allow_request: bool
    notes: str | None


class ModelChoice(BaseModel):
    llm_model: str
    label: str
    effort: bool


class ProfilesOut(BaseModel):
    models: list[ModelChoice]
    profiles: list[ProfileOut]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=255)
    llm_model: str | None = Field(default=None, max_length=100)
    llm_effort: Literal["low", "medium", "high"] | None = None
    llm_max_output_tokens: int | None = Field(default=None, ge=100, le=4000)
    tts_engine: Literal["generative", "neural", "standard"] | None = None
    voice_id: str | None = Field(default=None, max_length=40)
    llm_input_per_m: Price | None = Field(default=None, ge=0, le=1000, decimal_places=4)
    llm_cached_per_m: Price | None = Field(default=None, ge=0, le=1000, decimal_places=4)
    llm_cache_write_per_m: Price | None = Field(default=None, ge=0, le=1000, decimal_places=4)
    llm_output_per_m: Price | None = Field(default=None, ge=0, le=1000, decimal_places=4)
    tts_per_m_chars: Price | None = Field(default=None, ge=0, le=1000, decimal_places=4)
    stt_per_minute: Price | None = Field(default=None, ge=0, le=100, decimal_places=5)
    is_active: bool | None = None
    allow_request: bool | None = None
    notes: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _check(self) -> ProfileUpdate:
        _not_null(
            self,
            ("display_name", "llm_model", "llm_max_output_tokens", "tts_engine", "is_active", "allow_request",
             *admin.PRICE_FIELDS),
        )  # fmt: skip
        return self


def _profiles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"models": admin.models(), "profiles": rows}


@router.get("/profiles", response_model=ProfilesOut)
def profiles(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return _profiles(admin.list_profiles(db))


@router.patch("/profiles/{profile_id}", response_model=ProfilesOut)
def update_profile(
    body: ProfileUpdate,
    request: Request,
    profile_id: str = ProfileId,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    return _profiles(admin.update_profile(db, current, profile_id, changes, client_ip(request)))


@router.post("/profiles/{profile_id}/default", response_model=ProfilesOut)
def default_profile(
    request: Request,
    profile_id: str = ProfileId,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return _profiles(admin.make_default_profile(db, current, profile_id, client_ip(request)))


# --- Testers ---------------------------------------------------------------------------------------------


class TesterOut(BaseModel):
    id: int
    uid: int
    name: str
    trainings: list[str]
    is_active: bool
    created_by: str | None
    created_at: datetime
    code_changed_at: datetime | None


class TesterWithCode(BaseModel):
    """The access code is shown once: only its hash is kept."""

    tester: TesterOut
    code: str


class TesterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: int = Field(ge=1, le=4_294_967_295)
    name: str = Field(min_length=1, max_length=100)
    trainings: TRAINING_IDS

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the tester's name.")
        return value.strip()


class TesterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    trainings: TRAINING_IDS | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> TesterUpdate:
        _not_null(self, ("name", "trainings", "is_active"))
        return self


class TesterImportEntry(BaseModel):
    """One tester from testers.json, as web.py writes it."""

    model_config = ConfigDict(extra="ignore")

    uid: int = Field(ge=1, le=4_294_967_295)
    name: str = Field(min_length=1, max_length=100)
    trainings: list[str] = Field(max_length=50)
    code_sha256: str

    @field_validator("code_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        if not admin.is_hash(value):
            raise ValueError("Not a code hash.")
        return value


class TesterImport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    testers: list[TesterImportEntry] = Field(max_length=1000)


class ImportResult(BaseModel):
    added: list[int]
    skipped: list[int]
    dropped_trainings: dict[str, list[str]]


@router.get("/testers", response_model=list[TesterOut])
def testers(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return admin.list_testers(db)


@router.post("/testers", response_model=TesterWithCode, status_code=201)
def create_tester(
    body: TesterCreate,
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return admin.create_tester(db, current, body.model_dump(), client_ip(request))


@router.patch("/testers/{tester_id}", response_model=TesterOut)
def update_tester(
    body: TesterUpdate,
    request: Request,
    tester_id: int = Path(ge=1),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes:
        raise ApiError(422, "no_changes", "Nothing to change.")
    return admin.update_tester(db, current, tester_id, changes, client_ip(request))


@router.post("/testers/{tester_id}/new-code", response_model=TesterWithCode)
def new_code(
    request: Request,
    tester_id: int = Path(ge=1),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    """A new access code (shown once); the old one stops working."""
    return admin.reset_code(db, current, tester_id, client_ip(request))


@router.post("/testers/import", response_model=ImportResult)
def import_testers(
    body: TesterImport,
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Testers from the agent server's testers.json (its contents as JSON). Everyone keeps their code."""
    result = admin.import_testers(db, current, [e.model_dump() for e in body.testers], client_ip(request))
    result["dropped_trainings"] = {str(k): v for k, v in result["dropped_trainings"].items()}
    return result
