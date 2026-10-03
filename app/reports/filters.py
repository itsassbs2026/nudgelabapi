"""Global report filters (SPEC §7.1) and the WHERE clauses they turn into, used by every report.

Dates are calendar days in the signed-in user's time zone, inclusive, converted to the UTC instants the agent
stores. Org filters (region → market → district → store) apply to sessions by the store at session time, and
to trainees (progress, assignments) by their current store (SPEC §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, Query
from sqlalchemy import ColumnElement, and_, or_, select

from app.auth.deps import CurrentUser, get_user
from app.reference.agent_tables import (
    EXCLUDED_CLIENTS,
    training_sessions,
    trainings,
    vw_trainees,
    vw_training_stores,
)
from app.utils.errors import ApiError

DEFAULT_DAYS = 30
MAX_DAYS = 400


@dataclass(frozen=True)
class ReportFilters:
    date_from: date
    date_to: date
    start_utc: datetime  # inclusive, naive UTC
    end_utc: datetime  # exclusive, naive UTC
    timezone: str
    training_id: str | None = None
    completion_type: str | None = None
    region_id: int | None = None
    market_id: int | None = None
    district_id: int | None = None
    store_id: str | None = None
    profile_id: str | None = None
    voice_id: str | None = None
    include_bots: bool = False

    @property
    def has_org(self) -> bool:
        return any(v is not None for v in (self.region_id, self.market_id, self.district_id, self.store_id))


def build_filters(
    *,
    timezone: str,
    date_from: date | None = None,
    date_to: date | None = None,
    training_id: str | None = None,
    completion_type: str | None = None,
    region_id: int | None = None,
    market_id: int | None = None,
    district_id: int | None = None,
    store_id: str | None = None,
    profile_id: str | None = None,
    voice_id: str | None = None,
    include_bots: bool = False,
    today: date | None = None,
) -> ReportFilters:
    tz = ZoneInfo(timezone)
    today = today or datetime.now(tz).date()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=DEFAULT_DAYS - 1))
    if date_from > date_to:
        raise ApiError(422, "invalid_date_range", "date_from must be on or before date_to.")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise ApiError(422, "invalid_date_range", f"The date range can be at most {MAX_DAYS} days.")

    def to_utc(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)

    return ReportFilters(
        date_from=date_from,
        date_to=date_to,
        start_utc=to_utc(date_from),
        end_utc=to_utc(date_to + timedelta(days=1)),
        timezone=timezone,
        training_id=training_id,
        completion_type=completion_type,
        region_id=region_id,
        market_id=market_id,
        district_id=district_id,
        store_id=store_id,
        profile_id=profile_id,
        voice_id=voice_id,
        include_bots=include_bots,
    )


def report_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    training_id: str | None = Query(default=None, max_length=50),
    completion_type: str | None = Query(default=None, pattern="^(quiz|walkthrough|acknowledgment)$"),
    region_id: int | None = None,
    market_id: int | None = None,
    district_id: int | None = None,
    store_id: str | None = Query(default=None, max_length=12),
    profile_id: str | None = Query(default=None, max_length=30),
    voice_id: str | None = Query(default=None, max_length=40),
    include_bots: bool = False,
    current: CurrentUser = Depends(get_user),
) -> ReportFilters:
    """FastAPI dependency: filters from the query string, in the user's time zone."""
    if include_bots and not current.is_admin:
        raise ApiError(403, "forbidden", "Only Admins can include test sessions.")
    return build_filters(
        timezone=current.user.timezone,
        date_from=date_from,
        date_to=date_to,
        training_id=training_id,
        completion_type=completion_type,
        region_id=region_id,
        market_id=market_id,
        district_id=district_id,
        store_id=store_id,
        profile_id=profile_id,
        voice_id=voice_id,
        include_bots=include_bots,
    )


# -- WHERE clauses -------------------------------------------------------------------------------------------


def _org(table: object, f: ReportFilters) -> list[ColumnElement[bool]]:
    c = table.c  # type: ignore[attr-defined]
    out: list[ColumnElement[bool]] = []
    if f.region_id is not None:
        out.append(c.region_id == f.region_id)
    if f.market_id is not None:
        out.append(c.market_id == f.market_id)
    if f.district_id is not None:
        out.append(c.district_id == f.district_id)
    if f.store_id is not None:
        out.append(c.store_id == f.store_id)
    return out


def session_conditions(f: ReportFilters, *, in_range: bool = True) -> list[ColumnElement[bool]]:
    """Conditions on training_sessions (joined to trainings, and to vw_training_stores when org-filtered)."""
    s = training_sessions.c
    out: list[ColumnElement[bool]] = []
    if in_range:
        out += [s.started_at >= f.start_utc, s.started_at < f.end_utc]
    if not f.include_bots:
        out.append(or_(s.client.is_(None), s.client.not_in(EXCLUDED_CLIENTS)))
    if f.training_id:
        out.append(s.training_id == f.training_id)
    if f.completion_type:
        out.append(trainings.c.completion_type == f.completion_type)
    if f.profile_id:
        out.append(s.profile_id == f.profile_id)
    if f.voice_id:
        out.append(s.voice_id == f.voice_id)
    if f.has_org:
        out.append(
            s.store_id_at_session.in_(
                select(vw_training_stores.c.store_id).where(and_(*_org(vw_training_stores, f)))
            )
        )
    return out


def trainee_conditions(f: ReportFilters, uid_column: ColumnElement[int]) -> list[ColumnElement[bool]]:
    """Restricts a trainee-level query (progress, assignments) to trainees whose current store is in scope."""
    if not f.has_org:
        return []
    return [uid_column.in_(select(vw_trainees.c.uid).where(and_(*_org(vw_trainees, f))))]


def training_conditions(f: ReportFilters, training_column: ColumnElement[str]) -> list[ColumnElement[bool]]:
    out: list[ColumnElement[bool]] = []
    if f.training_id:
        out.append(training_column == f.training_id)
    if f.completion_type:
        out.append(
            training_column.in_(
                select(trainings.c.training_id).where(trainings.c.completion_type == f.completion_type)
            )
        )
    return out
