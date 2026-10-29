"""The Flutter app's training endpoints, under /app/v1 (docs/APP_HANDOFF.md §3).

Callers bring a NudgeLab pass (app/mobile/passes.py), never a dashboard login. The employee is always the
pass's uid: nothing here takes a uid from the request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.rate_limit import limiter
from app.config import Settings, get_settings
from app.db import get_db
from app.mobile import capacity, handoff, persona, sessions, trainings
from app.mobile.limits import reader, session_starter
from app.mobile.passes import Employee
from app.reference.agent_tables import trainings as trainings_table
from app.routers.auth import client_ip
from app.schemas.app import (
    HandoffIn,
    HandoffOut,
    PendingCount,
    RedeemIn,
    RedeemOut,
    SessionStartIn,
    SessionStartOut,
    TrainingList,
)
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
def pending_count(
    employee: Employee = Depends(reader),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return trainings.pending_count(db, settings, employee.uid, employee.profile)


@router.post("/trainings/{training_id}/session", response_model=SessionStartOut)
def start_session(
    request: Request,
    training_id: str = TrainingId,
    body: SessionStartIn = Body(default_factory=SessionStartIn),
    employee: Employee = Depends(session_starter),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return _start(db, settings, employee, training_id, body, client_ip(request))


def _assigned(db: Session, employee: Employee, training_id: str) -> Any:
    row = trainings.listed_training(db, employee.uid, training_id)
    if row is None:
        exists = db.execute(
            select(trainings_table.c.training_id).where(trainings_table.c.training_id == training_id)
        ).first()
        if exists is None:
            raise ApiError(404, "not_found", "Training not found.")
        raise ApiError(403, "not_assigned", "This training isn't assigned to you.")
    return row


def _start(
    db: Session,
    settings: Settings,
    employee: Employee,
    training_id: str,
    body: SessionStartIn,
    ip: str | None,
) -> dict[str, Any]:
    row = _assigned(db, employee, training_id)
    # Starting over runs the active version; otherwise a trainee mid-training stays on theirs.
    version = int(row.active_version_id) if body.start_over else persona.session_version(row)
    voices = persona.Voices(db)
    chosen = persona.choose(
        persona=persona.trainer_persona(
            employee.profile, row.profile_id, voices, persona.content_voices(db, {version}).get(version)
        ),
        default_name=persona.default_names(db, {version})[version],
        voices=voices,
        requested_name=body.trainer_name,
        requested_voice=body.trainer_voice,
    )
    capacity.check(db, settings)  # all trainers busy: 503 trainers_busy, the app says to come back later
    return sessions.start(db, settings, employee, training_id, chosen, start_over=body.start_over, ip=ip)


# --- The employee portal (2026-10-29, docs/PORTAL_TRAINING.md) ---------------------------------------------


@router.post("/handoffs", response_model=HandoffOut, status_code=201)
def create_handoff(
    body: HandoffIn,
    employee: Employee = Depends(session_starter),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """The portal's server asks for a one-time Start link for its signed-in employee's training."""
    _assigned(db, employee, body.training_id)
    return handoff.create(
        db, settings, employee, body.training_id, start_over=body.start_over, return_url=body.return_url
    )


def _redeem_limit() -> str:
    return get_settings().portal_redeem_rate_limit


@router.post("/handoffs/redeem", response_model=RedeemOut)
@limiter.limit(_redeem_limit)
def redeem_handoff(
    request: Request,
    body: RedeemIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """The training page trades the code (once) for the voice session. No pass: the code is the credential."""
    employee, row = handoff.redeem(db, body.code)
    started = _start(
        db, settings, employee, row.training_id, SessionStartIn(start_over=row.start_over), client_ip(request)
    )
    training = db.execute(
        select(trainings_table.c.title, trainings_table.c.app_title).where(
            trainings_table.c.training_id == row.training_id
        )
    ).first()
    name = str(employee.profile.get("first_name") or employee.profile.get("name") or "").split(" ")[0] or None
    return {
        **started,
        "training_id": row.training_id,
        "training_title": (training and (training.app_title or training.title)) or row.training_id,
        "return_url": row.return_url,
        "first_name": name,
    }
