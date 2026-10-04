"""Voices and voice samples (SPEC 10.3). Trainers and Admins listen; managing voices comes with Phase 14."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Response
from limits import parse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.auth.rate_limit import limiter
from app.config import Settings, get_settings
from app.db import get_db
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
