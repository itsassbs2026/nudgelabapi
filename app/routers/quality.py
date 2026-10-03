"""Quality queue (SPEC §8.2, Phase 5). Trainers and Admins can work the queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.reports import quality
from app.reports.filters import ReportFilters, report_filters
from app.routers.auth import client_ip
from app.schemas.reports import QualityPage, QualityState, QualityUpdate

router = APIRouter(tags=["quality"])


@router.get("/quality", response_model=QualityPage)
def list_quality(
    status: str | None = Query(default=None, pattern="^(open|reviewed|dismissed)$"),
    issue_type: str | None = Query(default=None, max_length=40),
    assignee_user_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
) -> Any:
    return quality.quality_list(
        db,
        f,
        status=status,
        issue_type=issue_type,
        assignee_user_id=assignee_user_id,
        page=page,
        page_size=page_size,
    )


@router.patch("/quality/{session_id}", response_model=QualityState)
def update_quality(
    body: QualityUpdate,
    request: Request,
    session_id: str = Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9-]+$"),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        changes.pop("status")  # status can't be cleared
    return quality.update_item(db, current.user, session_id, changes, ip=client_ip(request))
