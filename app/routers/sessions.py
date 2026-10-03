"""Sessions and recordings (SPEC §8.2, Phase 4). Trainers and Admins.

Viewing a transcript and getting a recording link are both written to the audit log (SPEC §12).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.config import Settings, get_settings
from app.db import get_db
from app.reports import sessions
from app.reports.filters import ReportFilters, report_filters
from app.routers.auth import client_ip
from app.schemas.reports import RecordingUrl, SessionDetail, SessionPage
from app.services import audit, recordings

router = APIRouter(tags=["sessions"])

SessionId = Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9-]+$")


@router.get("/sessions", response_model=SessionPage)
def list_sessions(
    uid: int | None = None,
    outcome: str | None = Query(default=None, pattern="^(passed|not_passed|no_quiz)$"),
    end_reason: str | None = Query(default=None, pattern="^(completed|user_left|error|dropped)$"),
    flagged: bool | None = None,
    min_rating: int | None = Query(default=None, ge=1, le=10),
    max_rating: int | None = Query(default=None, ge=1, le=10),
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return sessions.session_list(
        db,
        settings,
        f,
        uid=uid,
        outcome=outcome,
        end_reason=end_reason,
        flagged=flagged,
        min_rating=min_rating,
        max_rating=max_rating,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    request: Request,
    session_id: str = SessionId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    detail = sessions.session_detail(db, settings, session_id)
    audit.record(
        db,
        audit.AuditAction.TRANSCRIPT_VIEWED,
        actor_user_id=current.user.id,
        target_type="session",
        target_id=session_id,
        details={"uid": detail["session"]["uid"]},
        ip=client_ip(request),
    )
    db.commit()
    return detail


@router.post("/sessions/{session_id}/recording-url", response_model=RecordingUrl)
def recording_url(
    request: Request,
    session_id: str = SessionId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    key = sessions.recording_key(db, settings, session_id)
    url, expires_at = recordings.playback_url(settings, session_id, key)
    audit.record(
        db,
        audit.AuditAction.RECORDING_PLAYED,
        actor_user_id=current.user.id,
        target_type="session",
        target_id=session_id,
        details={"key": key, "expires_seconds": settings.recording_url_seconds},
        ip=client_ip(request),
    )
    db.commit()
    return {"url": url, "content_type": recordings.CONTENT_TYPE, "expires_at": expires_at}
