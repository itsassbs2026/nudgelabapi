"""Role Play report (docs/ROLEPLAY.md): practice conversations and their scores, per track, in the period.

Each row of `roleplay_attempts` is one practice conversation the grader scored (Beginner or the tougher Stress
practice). A person "opened the quiz" on a track once a Beginner practice scored the training's unlock score.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import roleplay_attempts, training_versions, trainings, vw_trainees
from app.reports.filters import ReportFilters, trainee_conditions, training_conditions

DEFAULT_UNLOCK = 4


def _unlock_scores(db: Session) -> dict[str, int]:
    """Each Role Play training's unlock score, from its live version (4 if it doesn't say)."""
    t, v = trainings.c, training_versions.c
    rows = db.execute(
        select(t.training_id, v.content)
        .join(training_versions, v.version_id == t.active_version_id)
        .where(t.completion_type == "roleplay")
    ).all()
    out = {}
    for training_id, content in rows:
        data = json.loads(content) if isinstance(content, (str, bytes)) else content or {}
        out[training_id] = int(
            ((data.get("roleplay") or {}).get("settings") or {}).get("unlock_score") or DEFAULT_UNLOCK
        )
    return out


def roleplay_report(
    db: Session, f: ReportFilters, *, track: str | None, tier: str | None, page: int, page_size: int
) -> dict[str, Any]:
    r = roleplay_attempts.c
    conditions: list[ColumnElement[bool]] = [
        r.started_at >= f.start_utc,
        r.started_at < f.end_utc,
        r.score.is_not(None),
        *training_conditions(f, r.training_id),
        *trainee_conditions(f, r.uid),
    ]
    if track:
        conditions.append(r.track == track)
    if tier:
        conditions.append(r.tier == tier)
    where = and_(*conditions)

    unlock = _unlock_scores(db)
    tracks = []
    grouped = db.execute(
        select(
            r.training_id, r.track, r.tier, func.count(), func.count(func.distinct(r.uid)), func.avg(r.score)
        )
        .where(where)
        .group_by(r.training_id, r.track, r.tier)
    ).all()
    by_track: dict[tuple[str, str], dict[str, Any]] = {}
    for training_id, track_id, level, n, people, avg in grouped:
        row = by_track.setdefault((training_id, track_id), {
            "training_id": training_id, "track": track_id, "practices": 0, "people": 0, "average_score": None,
            "stress_practices": 0, "stress_average": None, "opened_quiz": 0,
        })  # fmt: skip
        if level == "beginner":
            row.update(practices=int(n), people=int(people), average_score=round(float(avg), 1))
        else:
            row.update(stress_practices=int(n), stress_average=round(float(avg), 1))
    for (training_id, track_id), row in by_track.items():
        needed = unlock.get(training_id, DEFAULT_UNLOCK)
        opened = select(func.count(func.distinct(r.uid))).where(
            where, r.training_id == training_id, r.track == track_id, r.tier == "beginner", r.score >= needed
        )
        row["opened_quiz"] = int(db.execute(opened).scalar_one())
        row["unlock_score"] = needed
        tracks.append(row)
    titles = dict(db.execute(select(trainings.c.training_id, trainings.c.title)).all())
    for row in tracks:
        row["training_title"] = titles.get(row["training_id"])
    tracks.sort(key=lambda x: (x["training_title"] or "", x["track"]))

    t = vw_trainees.c
    q = (
        select(
            r.attempt_id,
            r.session_id,
            r.uid,
            t.name,
            t.store_name,
            r.training_id,
            trainings.c.title,
            r.track,
            r.tier,
            r.persona,
            r.score,
            r.quick_pauses,
            r.strength,
            r.gap,
            r.tip,
            r.started_at,
        )  # fmt: skip
        .select_from(
            roleplay_attempts.outerjoin(vw_trainees, t.uid == r.uid).outerjoin(
                trainings, trainings.c.training_id == r.training_id
            )
        )
        .where(where)
    )
    total = int(db.execute(select(func.count()).select_from(q.subquery())).scalar_one())
    rows = db.execute(q.order_by(r.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "tracks": tracks,
        "items": [
            {
                "attempt_id": x.attempt_id,
                "session_id": x.session_id,
                "uid": x.uid,
                "name": x.name,
                "store_name": x.store_name,
                "training_id": x.training_id,
                "training_title": x.title,
                "track": x.track,
                "tier": x.tier,
                "persona": x.persona,
                "score": x.score,
                "quick_pauses": x.quick_pauses,
                "strength": x.strength,
                "gap": x.gap,
                "tip": x.tip,
                "started_at": x.started_at,
            }  # fmt: skip
            for x in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
