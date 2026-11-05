"""Content recommendations in the Training studio (2026-11-05): trainers and Admins, like the whole Studio.

The nightly job (scripts/recommendations.py) writes them; here they're read and marked done or dismissed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.recommendations import service
from app.routers.auth import client_ip
from app.schemas.studio import TRAINING_ID_PATTERN

router = APIRouter(tags=["studio"])


class RecommendationOut(BaseModel):
    id: int
    training_id: str
    version_id: int | None
    area: str
    target: str | None
    impact: Literal["high", "medium", "low"]
    title: str
    problem: str
    suggestion: str
    evidence: str
    examples: list[str]
    status: Literal["open", "done", "dismissed"]
    status_note: str | None
    status_by: str | None
    status_at: datetime | None
    created_at: datetime


class LastRun(BaseModel):
    run_date: date
    window_from: date
    window_to: date
    calls: int


class TrainingRecommendations(BaseModel):
    training_id: str
    open: list[RecommendationOut]
    handled: list[RecommendationOut]
    last_run: LastRun | None


class StatusIn(BaseModel):
    status: Literal["open", "done", "dismissed"]
    note: str | None = Field(default=None, max_length=300)


@router.get("/trainings/{training_id}/recommendations", response_model=TrainingRecommendations)
def training_recommendations(
    training_id: str = Path(pattern=TRAINING_ID_PATTERN),
    _: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.for_training(db, training_id)


@router.patch("/recommendations/{rec_id}", response_model=RecommendationOut)
def update_recommendation(
    body: StatusIn,
    request: Request,
    rec_id: int = Path(ge=1),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.set_status(db, current, rec_id, body.status, body.note, client_ip(request))
