"""Assigning training from the dashboard (2026-10-07), and the per-role switches that allow it.

Each action has a permission (app/services/permissions.py): Admins always may; Trainers as an Admin sets on
Admin > Permissions. Assigning is always checked first (`/assignments/check` changes nothing) and the write
re-checks everything, so a stale check can't slip anything through.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user, require_admin
from app.db import get_db
from app.routers.auth import client_ip
from app.services import assignments, permissions
from app.services.permissions import Action
from app.utils.errors import ApiError

router = APIRouter(tags=["assignments"])


# -- permissions ---------------------------------------------------------------------------------------------


class PermissionRow(BaseModel):
    action: str
    label: str
    roles: dict[str, bool]


class PermissionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    role: str = Field(max_length=32)
    enabled: bool


class PermissionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[PermissionChange] = Field(min_length=1, max_length=50)


@router.get("/me/permissions", response_model=dict[str, bool])
def my_permissions(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    """What the signed-in user may do, so the dashboard shows only those buttons."""
    return permissions.mine(db, current)


@router.get("/admin/permissions", response_model=list[PermissionRow])
def permission_table(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return permissions.table(db)


@router.put("/admin/permissions", response_model=list[PermissionRow])
def set_permissions(
    request: Request,
    body: PermissionsIn,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return permissions.update(db, current, [c.model_dump() for c in body.changes], client_ip(request))


# -- assigning -----------------------------------------------------------------------------------------------


class PersonOut(BaseModel):
    uid: int
    name: str | None
    is_active: bool
    job_title: str | None
    store_id: str | None
    store_name: str | None


class AssignableTraining(BaseModel):
    training_id: str
    title: str
    app_title: str | None
    hidden: bool
    completion_key: str | None


class AssignIn(BaseModel):
    """One person (`uid`) or a CSV of uids (`csv`, with its `file_name`), the trainings, a due date if any."""

    model_config = ConfigDict(extra="forbid")

    uid: int | None = Field(default=None, gt=0)
    csv: str | None = Field(default=None, max_length=1_000_000)
    file_name: str | None = Field(default=None, max_length=255)
    training_ids: list[str] = Field(min_length=1, max_length=assignments.MAX_TRAININGS)
    due_date: date | None = None

    @model_validator(mode="after")
    def one_source(self) -> AssignIn:
        if (self.uid is None) == (self.csv is None):
            raise ValueError("Send either a uid or a CSV file.")
        return self


class TrainingOutcome(AssignableTraining):
    assign: int
    reactivate: int
    already: int


class ProblemOut(BaseModel):
    row: int | None
    value: str
    message: str


class WarningOut(BaseModel):
    uid: int
    name: str | None
    training_id: str
    message: str


class PersonPicked(BaseModel):
    uid: int
    name: str | None
    job_title: str | None
    store_id: str | None
    store_name: str | None


class AssignCounts(BaseModel):
    assign: int
    reactivate: int
    already: int
    problems: int
    warnings: int
    duplicates: int


class AssignReport(BaseModel):
    applied: bool
    people: int
    person: PersonPicked | None
    due_at: datetime | None
    counts: AssignCounts
    trainings: list[TrainingOutcome]
    problems: list[ProblemOut]
    warnings: list[WarningOut]


class IdsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_ids: list[int] = Field(min_length=1, max_length=assignments.MAX_IDS)


class DueDateIn(IdsIn):
    due_date: date | None


class CancelOut(BaseModel):
    cancelled: int
    already_cancelled: int
    passed: int
    not_found: int


class DueDateOut(BaseModel):
    updated: int
    skipped: int
    not_found: int
    due_at: datetime | None


def _can_assign(db: Session, current: CurrentUser) -> CurrentUser:
    if not any(permissions.allowed(db, current, a) for a in (Action.ASSIGN_SINGLE, Action.ASSIGN_BULK)):
        raise ApiError(403, "forbidden", "Your role can't assign training. An Admin can turn it on.")
    return current


def _plan(db: Session, current: CurrentUser, body: AssignIn) -> assignments.Plan:
    action = Action.ASSIGN_BULK if body.csv is not None else Action.ASSIGN_SINGLE
    if not permissions.allowed(db, current, action):
        what = "upload assignments" if body.csv is not None else "assign training"
        raise ApiError(403, "forbidden", f"Your role isn't allowed to {what}. An Admin can turn it on.")
    due_at = assignments.due_at_from(body.due_date, current.user.timezone)
    if body.csv is not None:
        parsed = assignments.parse_csv(body.csv)
        return assignments.plan(
            db, via="upload", rows=parsed.rows, training_ids=body.training_ids, due_at=due_at,
            problems=parsed.problems, duplicates=parsed.duplicates,
        )  # fmt: skip
    assert body.uid is not None
    return assignments.plan(db, via="dashboard", rows={body.uid: None}, training_ids=body.training_ids,
                            due_at=due_at)  # fmt: skip


@router.get("/assignments/people", response_model=list[PersonOut])
def find_people(
    q: str = Query(min_length=2, max_length=100),
    _: CurrentUser = Depends(permissions.require(Action.ASSIGN_SINGLE)),
    db: Session = Depends(get_db),
) -> Any:
    """Employees by name or uid, for the Assign training dialog."""
    return assignments.search_people(db, q)


@router.get("/assignments/trainings", response_model=list[AssignableTraining])
def trainings_to_assign(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    _can_assign(db, current)
    return assignments.assignable_trainings(db)


@router.post("/assignments/check", response_model=AssignReport)
def check_assignments(
    body: AssignIn, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    """What assigning would do, person by person and training by training. Changes nothing."""
    return _plan(db, current, body).report(applied=False)


@router.post("/assignments", response_model=AssignReport)
def assign(
    request: Request, body: AssignIn, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    """Assigns every valid row in one go (rows with problems are left out, as the check showed)."""
    the_plan = _plan(db, current, body)
    return assignments.apply(db, current, the_plan, file_name=body.file_name, ip=client_ip(request))


@router.post("/assignments/cancel", response_model=CancelOut)
def cancel_assignments(
    request: Request,
    body: IdsIn,
    current: CurrentUser = Depends(permissions.require(Action.ASSIGN_CANCEL)),
    db: Session = Depends(get_db),
) -> Any:
    return assignments.cancel(db, current, body.assignment_ids, client_ip(request))


@router.post("/assignments/due-date", response_model=DueDateOut)
def change_due_date(
    request: Request,
    body: DueDateIn,
    current: CurrentUser = Depends(permissions.require(Action.ASSIGN_DUE_DATE)),
    db: Session = Depends(get_db),
) -> Any:
    return assignments.change_due_date(db, current, body.assignment_ids, body.due_date, client_ip(request))
