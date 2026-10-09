"""Building and sending the daily summary email, and its recipient list (Admin > Daily summary).

The 7 AM timer (scripts/daily_summary.py) sends yesterday's summary to every active recipient, once per day:
`dash_summary_runs` records the day, so a rerun sends nothing. Emails go through the outbox, like every other
email, and the worker delivers them through Graph.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.dashboard import DashEmailOutbox, OutboxStatus
from app.models.summary import DashSummaryRecipient, DashSummaryRun
from app.notifications.render import render_html
from app.services import audit
from app.services.audit import AuditAction
from app.summary import narrative
from app.summary.data import daily_summary
from app.utils.errors import ApiError

TEMPLATE = "daily_summary"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def yesterday(settings: Settings, now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return (now.astimezone(ZoneInfo(settings.summary_timezone)) - timedelta(days=1)).date()


def _num(value: Any) -> str:
    return "–" if value is None else f"{value:,}"


def _view(data: dict[str, Any], words: dict[str, Any], by_claude: bool, settings: Settings) -> dict[str, Any]:
    k, b, w, a = data["kpis"], data["day_before"], data["week_average"], data["attention"]
    pct, count = narrative.pct, narrative.count

    def money(v: float | None) -> str:
        return "–" if v is None else f"${v:,.2f}"

    def rating(v: float | None) -> str:
        return "–" if v is None else f"{v:.1f} / 10"

    kpis = [
        {"label": "Calls", "value": _num(k["calls"]), "before": _num(b["calls"]), "week": _num(w["calls"])},
        {"label": "People who called", "value": _num(k["people"]), "before": _num(b["people"]), "week": "–"},
        {"label": "People who passed", "value": _num(k["passed"]), "before": _num(b["passed"]),
         "week": _num(w["passed"])},
        {"label": "Pass rate", "value": pct(k["pass_rate"]), "before": pct(b["pass_rate"]), "week": "–"},
        {"label": "Average rating", "value": rating(k["avg_rating"]), "before": rating(b["avg_rating"]),
         "week": "–"},
        {"label": "Cost per completion", "value": money(k["cost_per_completion"]),
         "before": money(b["cost_per_completion"]), "week": "–"},
    ]  # fmt: skip
    trainings = [
        {
            "title": t["title"],
            "people": _num(t["people"]),
            "passed": _num(t["passed"]),
            "pass_rate": pct(t["pass_rate"]),
            "avg_minutes": "–" if t["avg_minutes"] is None else f"{t['avg_minutes']} min",
            "overall": f"{t['passed_to_date']:,} of {t['assigned']:,} ({pct(t['overall_rate'])})"
            if t["assigned"]
            else "–",
        }
        for t in data["trainings"]
    ]
    attention = [
        text
        for n, text in (
            (a["never_connected"], f"{count(a['never_connected'], 'call')} never connected"),
            (
                a["stuck_people"],
                f"{count(a['stuck_people'], 'person', 'people')} stuck "
                f"({count(a['stuck_calls'], 'quick try', 'quick tries')})",
            ),
            (a["early_leaves"], f"{count(a['early_leaves'], 'hang-up')} in the first 2 minutes"),
            (a["low_ratings"], f"{count(a['low_ratings'], 'rating')} of 5 or less"),
            (k["flagged"], f"{count(k['flagged'], 'call')} flagged by the daily review"),
            (a["overdue"], f"{count(a['overdue'], 'overdue assignment')}"),
        )
        if n
    ]
    rp = data["roleplay"]
    day = date.fromisoformat(data["day"])
    return {
        "width": 680,
        "day_label": f"{day:%A}, {day:%b} {day.day}",
        "tz_label": "Central time" if data["timezone"] == "America/Chicago" else data["timezone"],
        "words": words,
        "by_claude": by_claude,
        "kpis": kpis,
        "trainings": trainings,
        "attention": attention,
        "roleplay": (
            f"{count(rp['practices'], 'practice')} by {count(rp['people'], 'person', 'people')}, "
            f"average Win Meter {rp['avg_score']} / 5"
            if rp
            else None
        ),
        "link": f"{settings.dashboard_base_url}/?range=custom&from={day}&to={day}",
    }


def build(db: Session, settings: Settings, day: date) -> tuple[str, str, bool]:
    """(subject, HTML, whether Claude wrote the highlights) for `day`."""
    data = daily_summary(db, day, settings.summary_timezone)
    words, by_claude = narrative.write(settings, data)
    subject = f"NudgeLab daily summary: {day:%a}, {day:%b} {day.day}"
    return subject, render_html(TEMPLATE, **_view(data, words, by_claude, settings)), by_claude


def _queue(db: Session, to: str, subject: str, html: str) -> None:
    db.add(
        DashEmailOutbox(
            to_email=to,
            subject=subject,
            body_html=html,
            template=TEMPLATE,
            status=OutboxStatus.PENDING.value,
            next_attempt_at=datetime.now(UTC),
        )
    )


def send_daily(db: Session, settings: Settings, day: date) -> int | None:
    """Queue `day`'s summary for every active recipient; None if that day was already sent. Commits."""
    if db.get(DashSummaryRun, day) is not None:
        return None
    to = [
        r.email
        for r in db.execute(select(DashSummaryRecipient).where(DashSummaryRecipient.is_active)).scalars()
    ]
    subject, html, by_claude = build(db, settings, day)
    for email in to:
        _queue(db, email, subject, html)
    db.add(DashSummaryRun(report_date=day, recipients=len(to), by_claude=by_claude))
    try:
        db.commit()
    except IntegrityError:  # another run recorded the day first
        db.rollback()
        return None
    return len(to)


def send_test(db: Session, settings: Settings, current: CurrentUser, ip: str | None) -> dict[str, Any]:
    """Yesterday's summary to the signed-in Admin only, now. Not recorded as the day's send."""
    day = yesterday(settings)
    subject, html, by_claude = build(db, settings, day)
    _queue(db, current.user.email, "[Test] " + subject, html)
    audit.record(db, AuditAction.SUMMARY_TEST_SENT, actor_user_id=current.user.id,
                 details={"day": day.isoformat(), "by_claude": by_claude}, ip=ip)  # fmt: skip
    db.commit()
    return {"to": current.user.email, "day": day, "by_claude": by_claude}


# -- recipients ---------------------------------------------------------------------------------------------


def _out(r: DashSummaryRecipient) -> dict[str, Any]:
    return {"id": r.id, "email": r.email, "is_active": r.is_active, "created_at": r.created_at}


def list_recipients(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(DashSummaryRecipient).order_by(DashSummaryRecipient.email)).scalars()
    return [_out(r) for r in rows]


def add_recipient(db: Session, current: CurrentUser, email: str, ip: str | None) -> dict[str, Any]:
    email = email.strip().lower()
    if not EMAIL.match(email) or len(email) > 255:
        raise ApiError(422, "bad_email", "Enter one email address, like name@primecomms.com.")
    if db.execute(
        select(DashSummaryRecipient).where(DashSummaryRecipient.email == email)
    ).scalar_one_or_none():
        raise ApiError(409, "already_listed", "That address is already on the list.")
    row = DashSummaryRecipient(email=email, is_active=True, added_by=current.user.id)
    db.add(row)
    audit.record(db, AuditAction.SUMMARY_RECIPIENTS_CHANGED, actor_user_id=current.user.id,
                 target_type="summary_recipient", target_id=email, details={"added": email},
                 ip=ip)  # fmt: skip
    db.commit()
    return _out(row)


def _get(db: Session, recipient_id: int) -> DashSummaryRecipient:
    row = db.get(DashSummaryRecipient, recipient_id)
    if row is None:
        raise ApiError(404, "not_found", "That recipient isn't on the list.")
    return row


def set_active(
    db: Session, current: CurrentUser, recipient_id: int, active: bool, ip: str | None
) -> dict[str, Any]:
    row = _get(db, recipient_id)
    row.is_active = active
    audit.record(db, AuditAction.SUMMARY_RECIPIENTS_CHANGED, actor_user_id=current.user.id,
                 target_type="summary_recipient", target_id=row.email,
                 details={"paused" if not active else "resumed": row.email}, ip=ip)  # fmt: skip
    db.commit()
    return _out(row)


def remove_recipient(db: Session, current: CurrentUser, recipient_id: int, ip: str | None) -> None:
    row = _get(db, recipient_id)
    audit.record(db, AuditAction.SUMMARY_RECIPIENTS_CHANGED, actor_user_id=current.user.id,
                 target_type="summary_recipient", target_id=row.email, details={"removed": row.email},
                 ip=ip)  # fmt: skip
    db.delete(row)
    db.commit()
