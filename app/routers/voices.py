"""Voices, voice samples and the setups to choose from (SPEC 10.3, 10.5). Trainers and Admins; managing
voices and setups is admin_stage2.py."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Response
from limits import parse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.auth.rate_limit import limiter
from app.config import Settings, get_settings
from app.db import get_db
from app.reference.agent_tables import training_profiles
from app.services import voices
from app.utils.errors import ApiError

router = APIRouter(tags=["voices"])


class VoiceOut(BaseModel):
    voice_id: str
    display_name: str
    language_code: str
    gender: str
    engine: str
    is_default: bool
    is_active: bool
    notes: str | None


class SetupChoice(BaseModel):
    profile_id: str
    display_name: str
    description: str | None
    is_default: bool


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Type something to read.")
        return value


@router.get("/voices", response_model=list[VoiceOut])
def list_voices(_: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    return voices.list_voices(db)


@router.post(
    "/voices/{voice_id}/sample",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}, "description": "The text read aloud, as MP3."}},
)
def voice_sample(
    body: SampleIn,
    voice_id: str = Path(min_length=1, max_length=40, pattern=r"^[A-Za-z-]+$"),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if len(body.text) > settings.voice_sample_max_chars:
        message = f"Samples can be up to {settings.voice_sample_max_chars} characters."
        raise ApiError(422, "text_too_long", message)
    limit = settings.voice_sample_rate_limit
    if not limiter.limiter.hit(parse(limit), "voice-sample", str(current.user.id)):
        raise ApiError(
            429, "rate_limited", "Too many samples. Wait a minute and try again.", {"limit": limit}
        )
    voice = voices.find_voice(db, voice_id)
    audio = voices.synthesize(settings, voice.voice_id, voice.engine or "generative", body.text)
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.get("/setups", response_model=list[SetupChoice])
def setups(_: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    """The setups that are switched on (for a preview call's choices); the default first."""
    p = training_profiles.c
    rows = db.execute(
        select(p.profile_id, p.display_name, p.description, p.is_default)
        .where(p.is_active == 1)
        .order_by(p.is_default.desc(), p.display_name)
    ).all()
    return [{**r._mapping, "is_default": bool(r.is_default)} for r in rows]
