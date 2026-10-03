"""The Flutter app's training endpoints, under /app/v1 (docs/APP_HANDOFF.md §3).

Callers bring a NudgeLab pass (app/mobile/passes.py), never a dashboard login. The employee is always the
pass's uid: nothing here takes a uid from the request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.mobile import sessions, trainings
from app.mobile.limits import reader, session_starter
from app.mobile.passes import Employee
from app.reference.agent_tables import trainings as trainings_table
from app.routers.auth import client_ip
from app.schemas.app import PendingCount, SessionStartIn, SessionStartOut, TrainingList
from app.utils.errors import ApiError

router = APIRouter(tags=["app"])

TrainingId = Path(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")


@router.get("/trainings", response_model=TrainingList)
def list_trainings(
    employee: Employee = Depends(reader),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return trainings.training_list(db, settings, employee.uid, employee.profile)


@router.get("/trainings/pending-count", response_model=PendingCount)
def pending_count(employee: Employee = Depends(reader), db: Session = Depends(get_db)) -> Any:
    return trainings.pending_count(db, employee.uid, employee.profile)


@router.post("/trainings/{training_id}/session", response_model=SessionStartOut)
def start_session(
    request: Request,
    training_id: str = TrainingId,
    body: SessionStartIn = Body(default_factory=SessionStartIn),
    employee: Employee = Depends(session_starter),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    if trainings.listed_training(db, employee.uid, training_id) is None:
        exists = db.execute(
            select(trainings_table.c.training_id).where(trainings_table.c.training_id == training_id)
        ).first()
        if exists is None:
            raise ApiError(404, "not_found", "Training not found.")
        raise ApiError(403, "not_assigned", "This training isn't assigned to you.")
    return sessions.start(
        db, settings, employee, training_id, start_over=body.start_over, ip=client_ip(request)
    )
