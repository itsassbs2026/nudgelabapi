"""The daily summary email's recipients, and a test send (Admin > Daily summary, 2026-10-09). Admin only."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, require_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.routers.auth import client_ip
from app.summary import send

router = APIRouter(prefix="/admin/summary", tags=["admin"])


class RecipientOut(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: datetime


class RecipientIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class RecipientUpdate(BaseModel):
    is_active: bool


class TestSentOut(BaseModel):
    to: str
    day: date
    by_claude: bool


@router.get("/recipients", response_model=list[RecipientOut])
def recipients(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return send.list_recipients(db)


@router.post("/recipients", response_model=RecipientOut, status_code=201)
def add_recipient(
    body: RecipientIn,
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return send.add_recipient(db, current, body.email, client_ip(request))


@router.patch("/recipients/{recipient_id}", response_model=RecipientOut)
def update_recipient(
    body: RecipientUpdate,
    request: Request,
    recipient_id: int = Path(ge=1),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return send.set_active(db, current, recipient_id, body.is_active, client_ip(request))


@router.delete("/recipients/{recipient_id}", status_code=204)
def remove_recipient(
    request: Request,
    recipient_id: int = Path(ge=1),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    send.remove_recipient(db, current, recipient_id, client_ip(request))
    return Response(status_code=204)


@router.post("/test", response_model=TestSentOut)
def send_test(
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Yesterday's summary, to you only, now. Takes up to a minute (the highlights are written by AI)."""
    return send.send_test(db, settings, current, client_ip(request))
