"""The Studio's "Check pronunciation" (2026-10-23): the acronyms in a draft and the voice that will say them.

The text to scan is sent from the editor (so unsaved edits are included); the version says which training and
setup, to work out which voice the call would use. Playing one goes through the existing voice sample endpoint
with `as_spoken` (app/routers/voices.py). Any signed-in Trainer or Admin may check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.mobile.persona import Voices
from app.reference.agent_tables import training_versions, trainings
from app.studio import pronunciation
from app.utils.errors import ApiError

router = APIRouter(tags=["studio"])


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: int
    content: dict[str, Any]


class AcronymOut(BaseModel):
    acronym: str
    spoken: str  # how it is said: the letters, one at a time
    count: int
    where: str  # the first place it appears
    example: str  # a sentence it appears in, to play


class CheckOut(BaseModel):
    voice_id: (
        str | None
    )  # the voice a call of this training uses (coach voice, else the setup's, else default)
    acronyms: list[AcronymOut]


@router.post("/studio/pronunciation/check", response_model=CheckOut)
def check(body: CheckIn, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    row = db.execute(
        select(trainings.c.profile_id)
        .join(training_versions, training_versions.c.training_id == trainings.c.training_id)
        .where(training_versions.c.version_id == body.version_id)
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "Version not found.")
    coach_voice = (body.content.get("training") or {}).get("voice")
    voice = Voices(db).for_training(row.profile_id, coach_voice if isinstance(coach_voice, str) else None)
    return {"voice_id": voice, "acronyms": pronunciation.scan(body.content)}
