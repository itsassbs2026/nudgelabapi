"""Admin: the Portal user/store sync, run on demand after an emailed code (app/reference/manual.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, require_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.reference import manual
from app.routers.auth import client_ip

router = APIRouter(prefix="/admin/reference-sync", tags=["admin"])


class SyncTable(BaseModel):
    table: str
    last_run_at: datetime | None
    status: str | None
    row_count: int | None
    error: str | None


class SyncTableResult(BaseModel):
    table: str
    status: str
    on_file: int | None
    fetched: int | None
    error: str | None


class ManualSync(BaseModel):
    id: int
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None
    tables: list[SyncTableResult]
    error: str | None


class SyncStatus(BaseModel):
    configured: bool
    schedule: str
    tables: list[SyncTable]
    manual: ManualSync | None


class CodeSent(BaseModel):
    sent_to: str
    expires_in: int


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\s*\d{6}\s*$")


class SyncQueued(BaseModel):
    job_id: int
    status: str


@router.get("", response_model=SyncStatus)
def sync_status(
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return manual.status(db, settings)


@router.post("/code", response_model=CodeSent)
def send_code(
    request: Request,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return manual.request_code(db, settings, current, client_ip(request))


@router.post("", response_model=SyncQueued, status_code=202)
def start_sync(
    request: Request,
    body: CodeIn,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return manual.confirm(db, settings, current, body.code, client_ip(request))
