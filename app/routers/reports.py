"""Report endpoints (SPEC §8.2, Phase 3).

Trainers and Admins; every one takes the global filters (SPEC §7.1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.config import Settings, get_settings
from app.db import get_db
from app.reports import cost, drilldown, options, overview, people, search, trainings
from app.reports.filters import ReportFilters, report_filters
from app.schemas.reports import (
    AcknowledgmentPage,
    AssignmentPage,
    CostOut,
    DrilldownOut,
    EmployeeOut,
    FeedbackPage,
    FilterOptions,
    LiveOut,
    OverviewOut,
    QuestionsOut,
    RatingTrendOut,
    SearchOut,
    TrainingDetailOut,
    TrainingsOut,
)
from app.services import live
from app.utils.errors import ApiError

router = APIRouter(tags=["reports"])


@router.get("/reports/overview", response_model=OverviewOut)
def get_overview(f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)) -> Any:
    return overview.overview(db, f)


@router.get("/reports/trainings", response_model=TrainingsOut)
def get_trainings(f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)) -> Any:
    return {"items": trainings.trainings_table(db, f)}


@router.get("/reports/trainings/{training_key}", response_model=TrainingDetailOut)
def get_training(
    training_key: str, f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)
) -> Any:
    # Named training_key so it doesn't clash with the training_id filter in the query string.
    return trainings.training_detail(db, f, training_key)


@router.get("/reports/trainings/{training_key}/questions", response_model=QuestionsOut)
def get_questions(
    training_key: str, f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)
) -> Any:
    return trainings.question_stats(db, f, training_key)


@router.get("/reports/drilldown", response_model=DrilldownOut)
def get_drilldown(
    level: drilldown.Level = "region",
    parent: str | None = Query(default=None, max_length=20),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
) -> Any:
    if level != "employee" and level != "store" and parent is not None and not parent.isdigit():
        raise ApiError(422, "invalid_parent", "parent must be a number for this level.")
    return drilldown.drilldown(db, f, level, parent)


@router.get("/reports/rating-trend", response_model=RatingTrendOut)
def get_rating_trend(f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)) -> Any:
    return people.rating_trend(db, f)


@router.get("/reports/cost", response_model=CostOut)
def get_cost(f: ReportFilters = Depends(report_filters), db: Session = Depends(get_db)) -> Any:
    return cost.cost_report(db, f)


@router.get("/reports/filter-options", response_model=FilterOptions)
def get_filter_options(
    district_id: int | None = None, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return options.filter_options(db, district_id=district_id)


@router.get("/employees/{uid}", response_model=EmployeeOut)
def get_employee(
    uid: int,
    include_bots: bool = False,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    if include_bots and not current.is_admin:
        raise ApiError(403, "forbidden", "Only Admins can include test sessions.")
    return people.employee(db, uid, include_bots=include_bots)


@router.get("/feedback", response_model=FeedbackPage)
def get_feedback(
    search: str | None = Query(default=None, max_length=100),
    min_rating: int | None = Query(default=None, ge=1, le=10),
    max_rating: int | None = Query(default=None, ge=1, le=10),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
) -> Any:
    return people.feedback_list(
        db, f, search=search, min_rating=min_rating, max_rating=max_rating, page=page, page_size=page_size
    )


@router.get("/acknowledgments", response_model=AcknowledgmentPage)
def get_acknowledgments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
) -> Any:
    return people.acknowledgments_list(db, f, page=page, page_size=page_size)


@router.get("/assignments", response_model=AssignmentPage)
def get_assignments(
    state: str | None = Query(default=None, pattern="^(not_started|in_progress|completed|overdue)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    f: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
) -> Any:
    return people.assignments_list(db, f, state=state, page=page, page_size=page_size)


@router.get("/search", response_model=SearchOut)
def quick_search(
    q: str = Query(min_length=2, max_length=100),
    _: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    """The ⌘K palette: employees by name or uid, sessions by id prefix, trainings and stores by name."""
    return search.search(db, q)


@router.get("/live", response_model=LiveOut)
async def live_sessions(
    include_bots: bool = False,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Training calls in progress right now (names and trainings only). Test calls are Admin-only."""
    if include_bots and not current.is_admin:
        raise ApiError(403, "forbidden", "Only Admins can include test sessions.")
    if not settings.livekit_configured:
        return {"configured": False, "rooms": []}
    rooms = await live.fetch_rooms(settings)
    described = await run_in_threadpool(live.describe, db, rooms, include_tests=include_bots)
    return {"configured": True, "rooms": described}
