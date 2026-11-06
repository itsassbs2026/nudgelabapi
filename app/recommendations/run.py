"""The nightly run: every live training with enough calls in the last week gets fresh recommendations, and the
NudgeLab team gets one email with what the content can't fix ("our list").

A training is looked at once per night: a rerun (after a failure or a reboot) skips trainings already
done that day, and sends the email only if it hasn't gone out yet.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.dashboard import DashEmailOutbox, OutboxStatus
from app.models.recommendations import (
    DISMISSED,
    DONE,
    OPEN,
    REPLACED,
    DashRecommendation,
    DashRecommendationRun,
)
from app.notifications.render import render_html
from app.recommendations import evidence as ev
from app.recommendations import writer
from app.reference.agent_tables import trainings
from app.reports.filters import build_filters

logger = logging.getLogger(__name__)

TEMPLATE = "recommendations_ours"
EARLIER_DAYS = 60


def today(settings: Settings, now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.summary_timezone)).date()


def live_trainings(db: Session) -> list[str]:
    t = trainings.c
    return [
        r[0]
        for r in db.execute(
            select(t.training_id)
            .where(t.active_version_id.is_not(None), t.status != "archived")
            .order_by(t.training_id)
        )
    ]


def _earlier(db: Session, training_id: str, run_date: date) -> list[dict[str, Any]]:
    """Recommendations trainers marked done or dismissed lately, so Claude doesn't suggest them again."""
    r = DashRecommendation
    since = datetime.combine(run_date - timedelta(days=EARLIER_DAYS), datetime.min.time())
    return [
        {"title": x.title, "target": x.target, "status": x.status, "trainer_note": x.status_note}
        for x in db.execute(
            select(r)
            .where(r.training_id == training_id, r.status.in_((DONE, DISMISSED)), r.created_at >= since)
            .order_by(r.created_at.desc())
            .limit(20)
        ).scalars()
    ]


def window(settings: Settings, run_date: date) -> tuple[date, date] | None:
    """The days a run on run_date looks at: the last recommendations_days, but none before
    recommendations_not_before. None when that leaves no days."""
    start = run_date - timedelta(days=settings.recommendations_days)
    if settings.recommendations_not_before:
        start = max(start, settings.recommendations_not_before)
    end = run_date - timedelta(days=1)
    return (start, end) if start <= end else None


def run_training(
    db: Session, settings: Settings, training_id: str, run_date: date
) -> DashRecommendationRun | None:
    """Look at one training's last week; None when it had too few calls. Commits."""
    days = window(settings, run_date)
    if days is None:
        return None
    f = build_filters(
        timezone=settings.summary_timezone, date_from=days[0], date_to=days[1], training_id=training_id
    )
    evidence = ev.gather(db, f)
    if evidence["calls"] < settings.recommendations_min_calls:
        return None
    run = DashRecommendationRun(
        run_date=run_date,
        training_id=training_id,
        version_id=evidence.get("live_version_id"),
        window_from=f.date_from,
        window_to=f.date_to,
        calls=evidence["calls"],
        model=settings.recommendations_model,
    )
    try:
        got, usage = writer.writer(settings, evidence, _earlier(db, training_id, run_date))
        recs, ours = writer.clean(got, evidence)
        run.input_tokens, run.output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        run.ours = ours
    except Exception as exc:  # noqa: BLE001 - one training's failure mustn't stop the others
        logger.warning("recommendations: %s failed (%s)", training_id, type(exc).__name__)
        run.error = f"{type(exc).__name__}: {exc}"[:500]
        db.add(run)
        db.commit()
        return run  # the earlier open recommendations stay up
    db.add(run)
    db.flush()
    r = DashRecommendation
    db.execute(update(r).where(r.training_id == training_id, r.status == OPEN).values(status=REPLACED))
    for rec in recs:
        db.add(
            DashRecommendation(
                run_id=run.id, training_id=training_id, version_id=run.version_id, status=OPEN, **rec
            )
        )
    db.commit()
    return run


def _email_sent(db: Session, run_date: date) -> bool:
    subject = _subject(run_date)
    return (
        db.execute(
            select(DashEmailOutbox.id).where(
                DashEmailOutbox.template == TEMPLATE, DashEmailOutbox.subject == subject
            )
        ).first()
        is not None
    )


def _subject(run_date: date) -> str:
    return f"NudgeLab recommendations: our list for {run_date:%a}, {run_date:%b} {run_date.day}"


def build_email(db: Session, settings: Settings, run_date: date) -> tuple[str, str]:
    """(subject, HTML) of the night's email: our list per training, and how many recommendations trainers
    got."""
    runs = list(
        db.execute(
            select(DashRecommendationRun)
            .where(DashRecommendationRun.run_date == run_date)
            .order_by(DashRecommendationRun.training_id)
        ).scalars()
    )
    titles = {tid: title for tid, title in db.execute(select(trainings.c.training_id, trainings.c.title))}
    counts: dict[str, int] = {}
    for (training_id,) in db.execute(
        select(DashRecommendation.training_id).where(
            DashRecommendation.run_id.in_([x.id for x in runs] or [0])
        )
    ):
        counts[training_id] = counts.get(training_id, 0) + 1
    link = settings.dashboard_base_url.rstrip("/")
    view = {
        "width": 680,
        "day_label": f"{run_date:%A}, {run_date:%b} {run_date.day}",
        "trainings": [
            {
                "title": titles.get(x.training_id, x.training_id),
                "calls": f"{x.calls:,}",
                "recommendations": counts.get(x.training_id, 0),
                "ours": [
                    {**o, "links": [f"{link}/sessions/{s}" for s in o.get("examples") or []]}
                    for o in x.ours or []
                ],
                "error": x.error,
            }
            for x in runs
        ],
        "studio_link": f"{link}/studio",
    }
    return _subject(run_date), render_html(TEMPLATE, **view)


def run_all(db: Session, settings: Settings, run_date: date | None = None) -> dict[str, Any]:
    run_date = run_date or today(settings)
    done = set(
        db.execute(
            select(DashRecommendationRun.training_id).where(DashRecommendationRun.run_date == run_date)
        ).scalars()
    )
    looked = skipped = failed = 0
    for training_id in live_trainings(db):
        if training_id in done:
            continue
        run = run_training(db, settings, training_id, run_date)
        if run is None:
            skipped += 1
        else:
            looked += 1
            failed += int(bool(run.error))
    emailed = False
    if settings.recommendations_email and not _email_sent(db, run_date):
        any_runs = db.execute(
            select(DashRecommendationRun.id).where(DashRecommendationRun.run_date == run_date)
        ).first()
        if any_runs:
            subject, html = build_email(db, settings, run_date)
            db.add(
                DashEmailOutbox(
                    to_email=settings.recommendations_email,
                    subject=subject,
                    body_html=html,
                    template=TEMPLATE,
                    status=OutboxStatus.PENDING.value,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            db.commit()
            emailed = True
    return {"date": run_date.isoformat(), "looked_at": looked, "too_few_calls": skipped, "failed": failed,
            "emailed": emailed}  # fmt: skip
