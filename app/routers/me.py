"""GET /api/v1/me, PATCH /api/v1/me (SPEC §8.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_current_user, get_user
from app.db import get_db
from app.schemas.auth import MeUpdate, UserOut
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
