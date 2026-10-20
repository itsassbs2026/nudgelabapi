"""GET /api/v1/me, PATCH /api/v1/me (SPEC §8.1), and the What's new announcements you've seen (2026-10-15)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_current_user, get_user
from app.db import get_db
from app.models.announcements import DashAnnouncementSeen
from app.schemas.auth import MeUpdate, UserOut
from app.services.company import company
from app.services.users import _check_timezone

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
def me(current: CurrentUser = Depends(get_current_user)) -> UserOut:
    """Works during a pending password change, so the dashboard knows to show the change-password screen."""
    return UserOut.model_validate(current.user)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: MeUpdate, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> UserOut:
    user = current.user
    if body.timezone is not None:
        _check_timezone(body.timezone)
        user.timezone = body.timezone
    if body.preferences is not None:
        user.preferences = body.preferences
    db.commit()
    return UserOut.model_validate(user)


@router.get("/me/announcements", response_model=list[str])
def announcements_seen(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> list[str]:
    """The What's new announcements you've already seen (their keys); the dashboard shows the newest
    other one."""
    return list(
        db.execute(
            select(DashAnnouncementSeen.announcement).where(DashAnnouncementSeen.user_id == current.user.id)
        ).scalars()
    )


@router.put("/me/announcements/{key}", status_code=204)
def mark_announcement_seen(
    key: str = Path(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Response:
    """Records that you've seen an announcement (again is fine: the first time is kept)."""
    row = insert(DashAnnouncementSeen).values(user_id=current.user.id, announcement=key)
    db.execute(row.prefix_with("IGNORE"))
    db.commit()
    return Response(status_code=204)


class CompanyOut(BaseModel):
    name: str
    description: str
    app_name: str
    portal_name: str
    email_domain: str


@router.get("/company", response_model=CompanyOut)
def company_info(_: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> CompanyOut:
    """Who this installation is for (Admin > Settings, company.*), so the dashboard names the right company
    and portal (2026-10-20: one codebase for Prime and Suncom)."""
    return CompanyOut(**company(db).as_dict())
