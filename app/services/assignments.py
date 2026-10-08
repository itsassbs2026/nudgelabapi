"""Assigning training from the dashboard (2026-10-07): one person, or a CSV of uids; cancelling; due dates.

The owner's database query and the dashboard share `training_assignments` (one row per person and training).
Rows made here are tagged `assigned_via` ('dashboard' or 'upload') and `assigned_by_user_id`, so the query can
leave them alone. Nothing is ever deleted: cancelling sets the status, and assigning a cancelled row again
re-activates it.

Every write is checked first, and the check is the same code as the write: `plan()` works out what would
happen and why, `apply()` runs it in one transaction. People come from `vw_trainees` only (no personal-data
columns).
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, CursorResult, and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.reference.agent_tables import (
    training_assignments,
    training_progress,
    training_versions,
    trainings,
    vw_trainees,
)
from app.reports.search import like_pattern
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

MAX_UPLOAD_ROWS = 5000
MAX_TRAININGS = 20
MAX_IDS = 500  # rows ticked on the Assignments page for one cancel or due-date change
MAX_LISTED = 1000  # problems and warnings sent back; the counts are always complete
MAX_DUE_YEARS = 3
_CHUNK = 1000
_UID_MAX = 4_294_967_295  # int unsigned


def _chunks(values: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for i in range(0, len(values), _CHUNK):
        yield values[i : i + _CHUNK]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- lookups -------------------------------------------------------------------------------------------------


def search_people(db: Session, q: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Employees by name or uid for the Assign dialog, current employees first."""
    term = q.strip()
    t = vw_trainees.c
    options: list[ColumnElement[bool]] = [t.name.like(like_pattern(term))]
    if term.isdigit():
        options.append(t.uid == int(term))
    rows = db.execute(
        select(t.uid, t.name, t.is_active, t.job_title, t.store_id, t.store_name)
        .where(or_(*options))
        .order_by(t.is_active.desc(), t.name)
        .limit(limit)
    ).all()
    return [
        {
            "uid": r.uid,
            "name": r.name,
            "is_active": bool(r.is_active),
            "job_title": r.job_title,
            "store_id": r.store_id,
            "store_name": r.store_name,
        }
        for r in rows
    ]


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _tracks(content: Any) -> tuple[list[dict[str, Any]], str | None]:
    """A Role Play version's tracks (id, name, the reasons that pick it) and its default track."""
    if isinstance(content, (str, bytes)):
        content = json.loads(content)
    rp = (content or {}).get("roleplay") or {}
    tracks = [{"id": t["id"], "name": t.get("name") or t["id"], "reasons": list(t.get("reasons") or [])}
              for t in rp.get("tracks") or []]  # fmt: skip
    return tracks, rp.get("default_track")


def assignable_trainings(db: Session) -> list[dict[str, Any]]:
    """Trainings that can be assigned: published (a live version) and not archived. `hidden` ones can be
    assigned but don't show in the app until an Admin shows them. A Role Play training lists its tracks: the
    reason a person is assigned picks theirs (docs/ROLEPLAY.md)."""
    t, v = trainings.c, training_versions.c
    rows = db.execute(
        select(
            t.training_id, t.title, t.app_title, t.app_status, t.completion_key, t.completion_type, v.content
        )
        .outerjoin(training_versions, v.version_id == t.active_version_id)
        .where(t.status != "retired", t.active_version_id.is_not(None))
        .order_by(t.title)
    ).all()
    out = []
    for r in rows:
        roleplay = r.completion_type == "roleplay"
        tracks, default = _tracks(r.content) if roleplay else ([], None)
        out.append({
            "training_id": r.training_id,
            "title": r.title,
            "app_title": r.app_title,
            "hidden": r.app_status != "active",
            "completion_key": r.completion_key,
            "completion_type": r.completion_type or "quiz",
            "tracks": [{"id": x["id"], "name": x["name"]} for x in tracks],
            "default_track": default,
            "_reasons": {key: x["name"] for x in tracks for key in (_norm(x["id"]), _norm(x["name"]),
                                                                    *(_norm(r) for r in x["reasons"]))},
        })  # fmt: skip
    return out


def public_training(training: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in training.items() if not k.startswith("_")}


def reason_for(training: dict[str, Any], reason: str | None) -> str | None:
    """The track name a reason picks in a Role Play training (stored as the assignment's ai_flag), None for no
    reason (the default track), or an error for one no track has. Other trainings keep no reason."""
    if training["completion_type"] != "roleplay" or not _norm(reason):
        return None
    name = training["_reasons"].get(_norm(reason))
    if name is None:
        names = ", ".join(x["name"] for x in training["tracks"])
        message = f"\"{reason}\" isn't one of {training['title']}'s reasons: {names}."
        raise ApiError(422, "reason_unknown", message, {"training_id": training["training_id"]})
    return str(name)


# -- input ---------------------------------------------------------------------------------------------------


@dataclass
class Problem:
    row: int | None
    value: str
    message: str


@dataclass
class ParsedUids:
    uids: list[int] = field(default_factory=list)
    rows: dict[int, int | None] = field(default_factory=dict)  # uid -> its first row in the file
    problems: list[Problem] = field(default_factory=list)
    duplicates: int = 0


def parse_csv(text: str) -> ParsedUids:
    """A CSV with a `uid` column (other columns are ignored), or just one uid per line. Row numbers are the
    file's own lines, header included, so they match what a spreadsheet shows."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    lines = [(n, [cell.strip() for cell in row]) for n, row in enumerate(reader, start=1)]
    lines = [(n, row) for n, row in lines if any(row)]
    if not lines:
        raise ApiError(422, "file_empty", "The file has no uids in it.")
    header = [cell.lower() for cell in lines[0][1]]
    if "uid" in header:
        column = header.index("uid")
        lines = lines[1:]
    elif lines[0][1][0].isdigit():
        column = 0  # no header row: the first column holds the uids
    else:
        raise ApiError(422, "no_uid_column", "The file needs a column headed uid (download the template).")
    if len(lines) > MAX_UPLOAD_ROWS:
        size, limit = f"{len(lines):,}", f"{MAX_UPLOAD_ROWS:,}"
        raise ApiError(422, "file_too_big", f"The file has {size} rows; the limit is {limit} per upload.")
    if not lines:
        raise ApiError(422, "file_empty", "The file has no uids in it.")
    out = ParsedUids()
    for n, row in lines:
        value = row[column] if column < len(row) else ""
        if not value:
            out.problems.append(Problem(n, "", "No uid in this row."))
            continue
        clean = value[:-2] if value.endswith(".0") else value  # a spreadsheet's 140128.0
        if not clean.isdigit() or not 0 < int(clean) <= _UID_MAX:
            out.problems.append(Problem(n, value[:40], "Not a uid (a uid is a whole number)."))
            continue
        uid = int(clean)
        if uid in out.rows:
            out.duplicates += 1
            continue
        out.rows[uid] = n
        out.uids.append(uid)
    return out


def due_at_from(due_date: date | None, timezone: str) -> datetime | None:
    """The end of the due day in the user's time zone, in UTC: an assignment due on the 31st is overdue from
    the 1st."""
    if due_date is None:
        return None
    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()
    if due_date < today:
        raise ApiError(422, "due_date_past", "The due date is in the past.")
    if due_date > today + timedelta(days=366 * MAX_DUE_YEARS):
        raise ApiError(422, "due_date_far", f"The due date is more than {MAX_DUE_YEARS} years away.")
    end = datetime.combine(due_date, time(23, 59, 59), tzinfo=tz)
    return end.astimezone(UTC).replace(tzinfo=None)


# -- plan and apply ------------------------------------------------------------------------------------------


@dataclass
class Plan:
    via: str  # dashboard | upload
    due_at: datetime | None
    trainings: list[dict[str, Any]]
    people: dict[int, dict[str, Any]]  # the valid people
    to_insert: list[tuple[int, str]] = field(default_factory=list)
    to_reactivate: list[int] = field(default_factory=list)  # assignment_ids
    # Role Play: assignments whose reason changes (their next session starts fresh on the new track).
    to_change_reason: list[tuple[int, str | None]] = field(default_factory=list)
    reasons: dict[str, str | None] = field(default_factory=dict)  # training_id -> the reason (ai_flag)
    per_training: dict[str, dict[str, int]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    duplicates: int = 0

    def report(self, *, applied: bool) -> dict[str, Any]:
        counts = {
            "assign": len(self.to_insert),
            "reactivate": len(self.to_reactivate),
            "reason_changed": len(self.to_change_reason),
            "already": sum(v["already"] for v in self.per_training.values()),
            "problems": len(self.problems),
            "warnings": len(self.warnings),
            "duplicates": self.duplicates,
        }
        person = next(iter(self.people.values())) if self.via == "dashboard" and self.people else None
        return {
            "applied": applied,
            "people": len(self.people),
            "person": person,
            "due_at": self.due_at,
            "counts": counts,
            "reason": next((r for r in self.reasons.values() if r), None),
            "trainings": [
                {**public_training(t), **self.per_training[t["training_id"]]} for t in self.trainings
            ],
            "problems": [p.__dict__ for p in self.problems[:MAX_LISTED]],
            "warnings": self.warnings[:MAX_LISTED],
        }


def _chosen_trainings(catalog: list[dict[str, Any]], training_ids: list[str]) -> list[dict[str, Any]]:
    wanted = list(dict.fromkeys(training_ids))
    known = {t["training_id"]: t for t in catalog}
    missing = [t for t in wanted if t not in known]
    if missing:
        raise ApiError(
            422,
            "training_not_assignable",
            "These trainings can't be assigned (unknown, archived or never published): " + ", ".join(missing),
            {"training_ids": missing},
        )
    return [known[t] for t in wanted]


def plan(
    db: Session,
    *,
    via: str,
    rows: dict[int, int | None],
    training_ids: list[str],
    due_at: datetime | None,
    problems: list[Problem] | None = None,
    duplicates: int = 0,
    reason: str | None = None,
) -> Plan:
    """What assigning `training_ids` to the uids in `rows` (uid -> file row) would do, and every reason a
    person is refused or worth a second look. Changes nothing."""
    catalog = assignable_trainings(db)
    chosen = _chosen_trainings(catalog, training_ids)
    result = Plan(via=via, due_at=due_at, trainings=chosen, people={}, problems=list(problems or []),
                  duplicates=duplicates)  # fmt: skip
    result.reasons = {t["training_id"]: reason_for(t, reason) for t in chosen}
    result.per_training = {
        t["training_id"]: {"assign": 0, "reactivate": 0, "reason_changed": 0, "already": 0} for t in chosen
    }
    uids = list(rows)
    t = vw_trainees.c
    found: dict[int, Any] = {}
    for part in _chunks(uids):
        for f in db.execute(
            select(t.uid, t.name, t.is_active, t.job_title, t.store_id, t.store_name).where(t.uid.in_(part))
        ):
            found[f.uid] = f
    for uid in uids:
        r = found.get(uid)
        if r is None:
            result.problems.append(Problem(rows[uid], str(uid), "No employee with this uid."))
        elif not r.is_active:
            left = f"{r.name or 'This person'} has left the company."
            result.problems.append(Problem(rows[uid], str(uid), left))
        else:
            result.people[uid] = {
                "uid": uid, "name": r.name, "job_title": r.job_title, "store_id": r.store_id,
                "store_name": r.store_name,
            }  # fmt: skip
    result.problems.sort(key=lambda p: (p.row is None, p.row or 0))
    valid = list(result.people)
    if not valid:
        return result

    # Versions of one Portal training share its completion key (e.g. RSM Fiber and RSM AIA).
    by_key: dict[str, list[str]] = defaultdict(list)
    for training in catalog:
        if training["completion_key"]:
            by_key[training["completion_key"]].append(training["training_id"])
    chosen_ids = [c["training_id"] for c in chosen]
    siblings = {
        c["training_id"]: [x for x in by_key.get(c["completion_key"] or "", []) if x != c["training_id"]]
        for c in chosen
    }
    titles = {c["training_id"]: c["title"] for c in catalog}
    look_at = sorted(set(chosen_ids) | {s for v in siblings.values() for s in v})

    a, p = training_assignments.c, training_progress.c
    existing: dict[tuple[int, str], Any] = {}
    passed: set[tuple[int, str]] = set()
    for part in _chunks(valid):
        for e in db.execute(
            select(a.assignment_id, a.uid, a.training_id, a.status, a.ai_flag).where(
                a.uid.in_(part), a.training_id.in_(look_at)
            )
        ):
            existing[(e.uid, e.training_id)] = e
        for g in db.execute(
            select(p.uid, p.training_id).where(
                p.uid.in_(part), p.training_id.in_(chosen_ids), p.passed_at.is_not(None)
            )
        ):
            passed.add((g.uid, g.training_id))

    kinds = {c["training_id"]: c["completion_type"] for c in chosen}
    for uid in valid:
        person = result.people[uid]
        for training_id in chosen_ids:
            row = existing.get((uid, training_id))
            counts = result.per_training[training_id]
            roleplay = kinds[training_id] == "roleplay"
            if row is not None and row.status != "cancelled":
                if roleplay and _norm(row.ai_flag) != _norm(result.reasons[training_id]):
                    result.to_change_reason.append((row.assignment_id, result.reasons[training_id]))
                    counts["reason_changed"] += 1
                    fresh = "New reason: their next session starts fresh (earlier progress is kept)."
                    result.warnings.append({"uid": uid, "name": person["name"], "training_id": training_id,
                                            "message": fresh})  # fmt: skip
                else:
                    counts["already"] += 1
                continue
            if row is not None:
                result.to_reactivate.append(row.assignment_id)
                counts["reactivate"] += 1
            else:
                result.to_insert.append((uid, training_id))
                counts["assign"] += 1
            notes = []
            if (uid, training_id) in passed:
                notes.append("Already passed this training, so it will show as completed.")
            for sibling in siblings[training_id]:
                other = existing.get((uid, sibling))
                if (other is not None and other.status != "cancelled") or sibling in chosen_ids:
                    title = titles.get(sibling, sibling)
                    notes.append(f"Also has {title}, another version of the same Portal training.")
            for note in notes:
                result.warnings.append({"uid": uid, "name": person["name"], "training_id": training_id,
                                        "message": note})  # fmt: skip
    return result


def apply(
    db: Session, current: CurrentUser, the_plan: Plan, *, file_name: str | None, ip: str | None
) -> dict[str, Any]:
    """Runs a plan in one transaction: every new and re-activated assignment, or none."""
    now = _now()
    a = training_assignments.c
    tag = {"assigned_via": the_plan.via, "assigned_by_user_id": current.user.id}
    try:
        for part in _chunks(the_plan.to_insert):
            db.execute(
                insert(training_assignments),
                [
                    {
                        "uid": uid,
                        "training_id": tid,
                        "assigned_at": now,
                        "due_at": the_plan.due_at,
                        "status": "assigned",
                        "ai_flag": the_plan.reasons.get(tid),
                        **tag,
                    }  # fmt: skip
                    for uid, tid in part
                ],
            )
        for ids in _chunks(the_plan.to_reactivate):
            outcome: CursorResult[Any] = db.execute(  # type: ignore[assignment]
                update(training_assignments)
                .where(a.assignment_id.in_(ids), a.status == "cancelled")
                .values(status="assigned", assigned_at=now, due_at=the_plan.due_at, **tag)
            )
            done = outcome.rowcount
            if done != len(ids):
                raise _changed_meanwhile()
            # A re-activated Role Play assignment takes the reason chosen now (other trainings keep theirs).
            for tid in (t["training_id"] for t in the_plan.trainings if t["completion_type"] == "roleplay"):
                db.execute(
                    update(training_assignments)
                    .where(a.assignment_id.in_(ids), a.training_id == tid)
                    .values(ai_flag=the_plan.reasons.get(tid))
                )
        for assignment_id, reason in the_plan.to_change_reason:
            db.execute(
                update(training_assignments)
                .where(a.assignment_id == assignment_id)
                .values(ai_flag=reason, **tag)
            )
    except IntegrityError as exc:
        db.rollback()
        raise _changed_meanwhile() from exc
    report = the_plan.report(applied=True)
    details: dict[str, Any] = {
        "via": the_plan.via,
        "trainings": [t["training_id"] for t in the_plan.trainings],
        "due_at": the_plan.due_at.isoformat() if the_plan.due_at else None,
        **{k: report["counts"][k] for k in ("assign", "reactivate", "reason_changed", "already", "problems")},
    }
    if report["reason"]:
        details["reason"] = report["reason"]
    if file_name:
        details["file_name"] = file_name[:200]
    if len(the_plan.people) <= 20:
        details["uids"] = sorted(the_plan.people)
    single = next(iter(the_plan.people)) if the_plan.via == "dashboard" and the_plan.people else None
    audit.record(db, AuditAction.ASSIGNMENTS_ADDED, actor_user_id=current.user.id, target_type="assignment",
                 target_id=single, details=details, ip=ip)  # fmt: skip
    db.commit()
    return report


def _changed_meanwhile() -> ApiError:
    return ApiError(
        409, "assignments_changed", "Someone changed these assignments just now. Check again, then assign."
    )


# -- cancel and due dates ------------------------------------------------------------------------------------


def _rows(db: Session, ids: list[int]) -> list[Any]:
    a, p = training_assignments.c, training_progress.c
    return list(
        db.execute(
            select(a.assignment_id, a.uid, a.training_id, a.status, p.passed_at)
            .outerjoin(training_progress, and_(p.uid == a.uid, p.training_id == a.training_id))
            .where(a.assignment_id.in_(list(dict.fromkeys(ids))))
        ).all()
    )


def _summary(rows: Sequence[Any]) -> list[str]:
    return [f"{r.uid}:{r.training_id}" for r in rows[:50]]


def cancel(db: Session, current: CurrentUser, ids: list[int], ip: str | None) -> dict[str, int]:
    """Cancels assignments. Nothing is deleted, progress is kept, and passes are never cancelled."""
    rows = _rows(db, ids)
    passed = [r for r in rows if r.passed_at is not None and r.status != "cancelled"]
    already = [r for r in rows if r.status == "cancelled"]
    todo = [r for r in rows if r.status != "cancelled" and r.passed_at is None]
    if todo:
        db.execute(
            update(training_assignments)
            .where(training_assignments.c.assignment_id.in_([r.assignment_id for r in todo]))
            .values(status="cancelled")
        )
    result = {
        "cancelled": len(todo),
        "already_cancelled": len(already),
        "passed": len(passed),
        "not_found": len(set(ids)) - len(rows),
    }
    if todo:
        details = {**result, "assignments": _summary(todo)}
        audit.record(db, AuditAction.ASSIGNMENTS_CANCELLED, actor_user_id=current.user.id,
                     target_type="assignment", details=details, ip=ip)  # fmt: skip
    db.commit()
    return result


def change_due_date(
    db: Session, current: CurrentUser, ids: list[int], due_date: date | None, ip: str | None
) -> dict[str, Any]:
    """Sets (or clears) the due date of open assignments. Cancelled and passed ones are left alone."""
    due_at = due_at_from(due_date, current.user.timezone)
    rows = _rows(db, ids)
    todo = [r for r in rows if r.status != "cancelled" and r.passed_at is None]
    if todo:
        db.execute(
            update(training_assignments)
            .where(training_assignments.c.assignment_id.in_([r.assignment_id for r in todo]))
            .values(due_at=due_at)
        )
    result = {
        "updated": len(todo),
        "skipped": len(rows) - len(todo),
        "not_found": len(set(ids)) - len(rows),
        "due_at": due_at,
    }
    if todo:
        audit.record(db, AuditAction.ASSIGNMENTS_DUE_DATE, actor_user_id=current.user.id,
                     target_type="assignment",
                     details={"updated": len(todo), "due_at": due_at.isoformat() if due_at else None,
                              "assignments": _summary(todo)}, ip=ip)  # fmt: skip
    db.commit()
    return result
