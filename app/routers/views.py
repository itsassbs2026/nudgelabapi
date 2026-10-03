"""Saved views and the team list (SPEC §6.3, Phase 8). Trainers and Admins.

A saved view is a named query string for one page (`range=7d&status=open&issue_type=tone`), private to the
person who saved it. The dashboard re-applies it by navigating to the page with that query.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.models.dashboard import DashUser, SavedView
from app.utils.errors import ApiError

router = APIRouter(tags=["views"])

Page = Literal["overview", "trainings", "drilldown", "sessions", "quality", "feedback", "cost", "compliance"]
MAX_VIEWS_PER_PAGE = 30
# Letters, digits and the characters a URL query uses; no spaces, quotes or angle brackets.
_QUERY = re.compile(r"^[A-Za-z0-9_.~%&=+\-]*$")


class SavedViewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: Page
    name: str = Field(min_length=1, max_length=100)
    query: str = Field(max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Give the view a name.")
        return value

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        value = value.lstrip("?")
        if not _QUERY.match(value):
            raise ValueError("Not a valid query string.")
        return value


class SavedViewOut(BaseModel):
    id: int
    page: str
    name: str
    query: str


class TeamMember(BaseModel):
    id: int
    full_name: str
    role: str


def _out(view: SavedView) -> dict[str, Any]:
    return {"id": view.id, "page": view.page, "name": view.name, "query": str(view.filters.get("query", ""))}


@router.get("/saved-views", response_model=list[SavedViewOut])
def list_views(
    page: Page = Query(),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    views = db.scalars(
        select(SavedView)
        .where(SavedView.user_id == current.user.id, SavedView.page == page)
        .order_by(SavedView.name, SavedView.id)
    ).all()
    return [_out(v) for v in views]


@router.post("/saved-views", response_model=SavedViewOut, status_code=status.HTTP_201_CREATED)
def save_view(
    body: SavedViewIn, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    count = db.execute(
        select(func.count()).where(SavedView.user_id == current.user.id, SavedView.page == body.page)
    ).scalar_one()
    if count >= MAX_VIEWS_PER_PAGE:
        raise ApiError(422, "too_many_views", f"You can save up to {MAX_VIEWS_PER_PAGE} views per page.")
    view = SavedView(user_id=current.user.id, page=body.page, name=body.name, filters={"query": body.query})
    db.add(view)
    db.commit()
    return _out(view)


@router.delete("/saved-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(
    view_id: int = Path(ge=1), current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> None:
    view = db.get(SavedView, view_id)
    if view is None or view.user_id != current.user.id:
        raise ApiError(404, "not_found", "Saved view not found.")
    db.delete(view)
    db.commit()


@router.get("/team", response_model=list[TeamMember])
def team(_: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    """Active dashboard users (names and roles only), for choosing who works a quality-queue item."""
    users = db.scalars(
        select(DashUser).where(DashUser.is_active.is_(True)).order_by(DashUser.full_name)
    ).all()
    return [{"id": u.id, "full_name": u.full_name, "role": u.role} for u in users]
