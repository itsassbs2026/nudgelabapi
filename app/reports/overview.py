"""Overview page (SPEC §7.2): KPIs, cohort progress, daily trend and completions by training."""

from __future__ import annotations

import dataclasses
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.reference.agent_tables import trainings
from app.reports import metrics
from app.reports.filters import ReportFilters


def overview(db: Session, f: ReportFilters) -> dict[str, Any]:
    titles = {tid: title for tid, title in db.execute(select(trainings.c.training_id, trainings.c.title))}
    per_training = metrics.per_training_activity(db, f)
    return {
        "period": {"date_from": f.date_from, "date_to": f.date_to, "timezone": f.timezone},
        "activity": dataclasses.asdict(metrics.activity(db, f)),
        "funnel": dataclasses.asdict(metrics.funnel(db, f)),
        "daily": metrics.daily_series(db, f),
        "by_training": sorted(
            (
                {
                    "training_id": tid,
                    "title": titles.get(tid, tid),
                    "sessions": v.get("sessions", 0),
                    "completions": v.get("completions", 0),
                }
                for tid, v in per_training.items()
            ),
            key=lambda r: (-r["completions"], -r["sessions"], str(r["title"])),
        ),
    }
