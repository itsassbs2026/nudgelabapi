"""Drill-down: region → market → district → store → employee (SPEC §7.2).

Every figure groups trainees by their **current** place in the hierarchy (vw_trainees), so a node's numbers
always add up to its parent's. Rows: cohort and completion rate (same basis as everywhere), sessions, average
rating and last activity in the period.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any, Literal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import training_feedback, training_sessions, trainings, vw_trainees
from app.reports import metrics
from app.reports.filters import ReportFilters, session_conditions
from app.utils.errors import ApiError

Level = Literal["region", "market", "district", "store", "employee"]

# level → (id column, name column, parent filter field, parent level)
LEVELS: dict[str, tuple[str, str, str | None]] = {
    "region": ("region_id", "region_name", None),
    "market": ("market_id", "market_name", "region_id"),
    "district": ("district_id", "district_name", "market_id"),
    "store": ("store_id", "store_name", "district_id"),
    "employee": ("uid", "name", "store_id"),
}


def drilldown(db: Session, f: ReportFilters, level: Level, parent: str | None) -> dict[str, Any]:
    id_col, name_col, parent_field = LEVELS[level]
    if parent_field and parent is None:
        raise ApiError(
            422, "parent_required", f"Choose a {parent_field.removesuffix('_id')} to see its {level}s."
        )
    scope = f
    if parent_field:
        value: Any = parent if parent_field == "store_id" else int(parent or 0)
        scope = replace(f, **{parent_field: value})

    basis, members = metrics.cohort(db, scope)
    uids = {m.uid for m in members}
    s = training_sessions.c
    cond = and_(*session_conditions(scope))
    sessions_by_uid: dict[int, tuple[int, Any]] = {}
    for uid, n, last in db.execute(
        select(s.uid, func.count(), func.max(s.started_at))
        .select_from(training_sessions.outerjoin(trainings, trainings.c.training_id == s.training_id))
        .where(cond)
        .group_by(s.uid)
    ):
        sessions_by_uid[int(uid)] = (int(n), last)
    ratings_by_uid: dict[int, list[int]] = defaultdict(list)
    for uid, rating in db.execute(
        select(training_feedback.c.uid, training_feedback.c.rating)
        .select_from(
            training_sessions.join(
                training_feedback, training_feedback.c.session_id == s.session_id
            ).outerjoin(trainings, trainings.c.training_id == s.training_id)
        )
        .where(cond, training_feedback.c.rating.is_not(None))
    ):
        ratings_by_uid[int(uid)].append(int(rating))

    everyone = uids | set(sessions_by_uid)
    if not everyone:
        return {"level": level, "parent": parent, "basis": basis, "rows": []}
    t = vw_trainees.c
    place = {int(r.uid): r for r in db.execute(select(vw_trainees).where(t.uid.in_(everyone)))}

    nodes: dict[Any, dict[str, Any]] = {}

    def node_for(uid: int) -> dict[str, Any] | None:
        r = place.get(uid)
        key = getattr(r, id_col) if r is not None else None
        if key is None:
            return None  # trainee not in the employee records (or no store)
        node = nodes.get(key)
        if node is None:
            node = nodes[key] = {
                "id": str(key),
                "name": getattr(r, name_col),
                "cohort": 0,
                "completed": 0,
                "sessions": 0,
                "trainees_active": 0,
                "_ratings": [],
                "last_activity": None,
            }
        return node

    for m in members:
        node = node_for(m.uid)
        if node is not None:
            node["cohort"] += 1
            node["completed"] += int(m.completed)
    for uid, (n, last) in sessions_by_uid.items():
        node = node_for(uid)
        if node is not None:
            node["sessions"] += n
            node["trainees_active"] += 1
            if last is not None and (node["last_activity"] is None or last > node["last_activity"]):
                node["last_activity"] = last
            node["_ratings"] += ratings_by_uid.get(uid, [])

    rows = []
    for node in nodes.values():
        ratings = node.pop("_ratings")
        node["completion_rate"] = metrics.ratio(node["completed"], node["cohort"])
        node["avg_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else None
        rows.append(node)
    rows.sort(key=lambda r: (str(r["name"] or ""), r["id"]))
    return {"level": level, "parent": parent, "basis": basis, "rows": rows}
