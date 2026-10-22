"""Reset a person's training progress (2026-10-22), from the employee's page.
See app/services/progress_reset.py.

Allowed for Admins always, and for Trainers unless an Admin switches `reset_progress` off on
Admin > Permissions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.db import get_db
from app.routers.auth import client_ip
from app.services import permissions, progress_reset
from app.services.permissions import Action

router = APIRouter(tags=["progress"])


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: str | None = Field(
        default=None, max_length=50
    )  # absent: every training they have progress on
    reason: str | None = Field(default=None, max_length=200)


class ResetTraining(BaseModel):
    training_id: str
    title: str
    was_passed: bool
    portal: str  # queued: Prime Portal's record is being cleared; not_linked: the training has no Portal key


class ResetOut(BaseModel):
    reset: list[ResetTraining]
    skipped: int  # trainings with no progress to reset


@router.post("/employees/{uid}/reset-progress", response_model=ResetOut)
def reset_progress(
    uid: int,
    body: ResetIn,
    request: Request,
    current: CurrentUser = Depends(permissions.require(Action.RESET_PROGRESS)),
    db: Session = Depends(get_db),
) -> Any:
    reason = (body.reason or "").strip() or None
    return progress_reset.reset(db, current, uid, body.training_id, reason, client_ip(request))
