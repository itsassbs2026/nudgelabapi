"""Quick search for the dashboard's ⌘K palette (SPEC §13): employees, sessions and trainings."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import training_sessions, trainings, vw_trainees, vw_training_stores

_SESSION_ID = re.compile(r"^[0-9a-fA-F-]{4,36}$")


def like_pattern(term: str) -> str:
    # Escape LIKE's own characters, so searching for "50%" finds the text 50%, not "50 then anything".
    return "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def search(db: Session, q: str, *, limit: int = 8) -> dict[str, Any]:
    term = q.strip()
    t = vw_trainees.c
    people = select(t.uid, t.name, t.store_name, t.is_active)
    if term.isdigit():
        people = people.where(or_(t.uid == int(term), t.name.like(like_pattern(term))))
    else:
        people = people.where(t.name.like(like_pattern(term)))
    employees = [
        {"uid": int(r.uid), "name": r.name, "store_name": r.store_name, "is_active": bool(r.is_active)}
        for r in db.execute(people.order_by(t.is_active.desc(), t.name).limit(limit))
    ]

    sessions: list[dict[str, Any]] = []
    if _SESSION_ID.match(term) and not term.isdigit():
        s = training_sessions.c
        for r in db.execute(
            select(s.session_id, s.started_at, s.uid, t.name, trainings.c.title)
            .select_from(
                training_sessions.outerjoin(vw_trainees, t.uid == s.uid).outerjoin(
                    trainings, trainings.c.training_id == s.training_id
                )
            )
            .where(s.session_id.like(term.lower() + "%"))
            .order_by(s.started_at.desc())
            .limit(5)
        ):
            sessions.append(
                {
                    "session_id": r.session_id,
                    "started_at": r.started_at,
                    "uid": r.uid,
                    "name": r.name,
                    "training_title": r.title,
                }
            )

    tr = trainings.c
    found = [
        {"training_id": r.training_id, "title": r.title}
        for r in db.execute(
            select(tr.training_id, tr.title)
            .where(or_(tr.title.like(like_pattern(term)), tr.training_id.like(like_pattern(term))))
            .order_by(tr.title)
            .limit(5)
        )
    ]

    st = vw_training_stores.c
    stores = [
        {"store_id": r.store_id, "store_name": r.store_name, "district_id": r.district_id}
        for r in db.execute(
            select(st.store_id, st.store_name, st.district_id)
            .where(st.store_active == 1, or_(st.store_id == term, st.store_name.like(like_pattern(term))))
            .order_by(st.store_name)
            .limit(5)
        )
    ]
    return {"employees": employees, "sessions": sessions, "trainings": found, "stores": stores}
