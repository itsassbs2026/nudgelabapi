"""An Admin's "Sync user/store list from Portal", confirmed with a code sent to their email (2026-10-06).

1. `request_code`: a 6-digit code, emailed to the Admin (outbox), valid 10 minutes. At most 3 a quarter hour;
   a new code cancels the previous one.
2. `confirm`: the right code (5 tries) queues one `reference_sync` job; only one runs at a time.
3. The worker (`run_reference_sync_jobs`) runs the same sync as the twice-daily timer (app/reference/sync.py),
   with the sync's own logins from the settings; each table's result is in `sync_run_log` as usual.

Codes are stored as an HMAC with the server's secret, never in clear. Every step is in the audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.action_codes import DashActionCode
from app.models.dashboard import DashEmailOutbox, Job, JobStatus, JobType, OutboxStatus
from app.models.sync_run_log import SyncRunLog
from app.notifications.render import render_html
from app.reference.sync import SOURCES, SYNC_NAME, source_fetcher, sync_reference_tables
from app.services import audit
from app.services.audit import AuditAction
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

PURPOSE = "reference_sync"
CODE_MINUTES = 10
MAX_ATTEMPTS = 5
CODES_PER_WINDOW = 3
CODE_WINDOW = timedelta(minutes=15)
STALE_RUNNING = timedelta(minutes=15)
SCHEDULE = "Every day at 8:00 AM and 11:00 PM Central"
# The timer's OnCalendar times (deploy/nudgelabapi-reference-sync.timer).
SCHEDULE_TZ = ZoneInfo("America/Chicago")
SCHEDULE_TIMES = (time(8, 0), time(23, 0))
RUN_GAP = timedelta(minutes=10)  # rows of one run start within minutes of each other


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(settings: Settings, user_id: int, code: str) -> str:
    message = f"{PURPOSE}:{user_id}:{code}".encode()
    return hmac.new(settings.jwt_secret.encode(), message, hashlib.sha256).hexdigest()


def mask_email(email: str) -> str:
    """b****@primecomms.com"""
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'*' * max(len(name) - 1, 3)}@{domain}"


def configured(settings: Settings) -> bool:
    return bool(settings.portallive_database_url and settings.reference_sync_database_url)


def _active_job(db: Session) -> Job | None:
    return db.scalars(
        select(Job)
        .where(
            Job.type == JobType.REFERENCE_SYNC.value,
            Job.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)),
        )
        .order_by(Job.id.desc())
        .limit(1)
    ).first()


def request_code(db: Session, settings: Settings, current: CurrentUser, ip: str | None) -> dict[str, Any]:
    if not configured(settings):
        raise ApiError(503, "sync_not_configured", "The Portal sync isn't set up on the server yet.")
    user, now = current.user, _now()
    recent = db.execute(
        select(func.count()).where(
            DashActionCode.user_id == user.id,
            DashActionCode.purpose == PURPOSE,
            DashActionCode.created_at >= now - CODE_WINDOW,
        )
    ).scalar_one()
    if recent >= CODES_PER_WINDOW:
        raise ApiError(
            429, "too_many_codes", "Too many codes asked for. Please wait 15 minutes and try again."
        )
    db.execute(  # a new code replaces any earlier one
        update(DashActionCode)
        .where(
            DashActionCode.user_id == user.id,
            DashActionCode.purpose == PURPOSE,
            DashActionCode.used_at.is_(None),
        )
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(DashActionCode(user_id=user.id, purpose=PURPOSE, code_hash=_hash(settings, user.id, code),
                          expires_at=now + timedelta(minutes=CODE_MINUTES), attempts=0))  # fmt: skip
    db.add(
        DashEmailOutbox(
            to_email=user.email,
            subject=f"NudgeLab code: {code}",
            body_html=render_html(
                "action_code",
                code=code,
                expires_minutes=CODE_MINUTES,
                action="sync the user and store list from Prime Portal",
            ),  # fmt: skip
            template="action_code",
            status=OutboxStatus.PENDING.value,
            next_attempt_at=datetime.now(UTC),
        )
    )
    audit.record(db, AuditAction.REFERENCE_SYNC_CODE_SENT, actor_user_id=user.id, target_type="sync",
                 target_id=SYNC_NAME, ip=ip)  # fmt: skip
    db.commit()
    return {"sent_to": mask_email(user.email), "expires_in": CODE_MINUTES * 60}


def confirm(
    db: Session, settings: Settings, current: CurrentUser, code: str, ip: str | None
) -> dict[str, Any]:
    user, now = current.user, _now()
    row = db.scalars(
        select(DashActionCode)
        .where(
            DashActionCode.user_id == user.id,
            DashActionCode.purpose == PURPOSE,
            DashActionCode.used_at.is_(None),
        )
        .order_by(DashActionCode.id.desc())
        .limit(1)
    ).first()
    if row is None or row.expires_at <= now:
        raise ApiError(400, "code_expired", "That code has expired or was already used. Ask for a new one.")
    if not hmac.compare_digest(row.code_hash, _hash(settings, user.id, code.strip())):
        row.attempts += 1
        left = MAX_ATTEMPTS - row.attempts
        if left <= 0:
            row.used_at = now
        audit.record(db, AuditAction.REFERENCE_SYNC_CODE_FAILED, actor_user_id=user.id, target_type="sync",
                     target_id=SYNC_NAME, details={"attempts_left": max(left, 0)}, ip=ip)  # fmt: skip
        db.commit()
        if left <= 0:
            raise ApiError(400, "code_locked", "Too many wrong codes. Ask for a new one.")
        raise ApiError(400, "code_wrong", "That code isn't right.", {"attempts_left": left})
    row.used_at = now
    if _active_job(db) is not None:
        db.commit()
        raise ApiError(409, "sync_running", "A sync is already running. Wait for it to finish.")
    job = Job(type=JobType.REFERENCE_SYNC.value, status=JobStatus.QUEUED.value,
              input={"requested_by": user.email}, created_by=user.id, attempts=0)  # fmt: skip
    db.add(job)
    db.flush()
    audit.record(db, AuditAction.REFERENCE_SYNC_STARTED, actor_user_id=user.id, target_type="sync",
                 target_id=SYNC_NAME, details={"job_id": job.id}, ip=ip)  # fmt: skip
    db.commit()
    return {"job_id": job.id, "status": job.status}


def _job_out(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {"id": job.id, "status": job.status, "created_at": job.created_at, "started_at": job.started_at,
            "finished_at": job.finished_at, "requested_by": (job.input or {}).get("requested_by"),
            "tables": (job.result or {}).get("tables", []), "error": job.error}  # fmt: skip


def next_scheduled(now: datetime) -> datetime:
    """The timer's next run after `now` (naive UTC), in naive UTC."""
    local = now.replace(tzinfo=UTC).astimezone(SCHEDULE_TZ)
    for days in range(3):
        day = local.date() + timedelta(days=days)
        for at in SCHEDULE_TIMES:
            when = datetime.combine(day, at, tzinfo=SCHEDULE_TZ)
            if when > local:
                return when.astimezone(UTC).replace(tzinfo=None)
    raise AssertionError("unreachable")


def last_automatic(db: Session) -> dict[str, Any] | None:
    """The latest run of the timer: sync_run_log rows that no manual job's run window covers (2026-10-07: the
    page showed only the manual run's time, so this morning's automatic run looked missing)."""
    windows = [
        (job.started_at - timedelta(seconds=5), (job.finished_at or _now()) + timedelta(seconds=5))
        for job in db.scalars(
            select(Job).where(Job.type == JobType.REFERENCE_SYNC.value, Job.started_at.is_not(None))
            .order_by(Job.id.desc()).limit(50)
        ).all()
        if job.started_at
    ]  # fmt: skip
    rows = db.scalars(
        select(SyncRunLog).where(SyncRunLog.sync_name == SYNC_NAME).order_by(SyncRunLog.id.desc()).limit(200)
    ).all()
    automatic = [r for r in rows if not any(start <= r.started_at <= end for start, end in windows)]
    if not automatic:
        return None
    first = automatic[0]
    run = [r for r in automatic if abs(r.started_at - first.started_at) <= RUN_GAP]
    return {
        "at": max(r.finished_at or r.started_at for r in run),
        "status": "failed" if any(r.status != "success" for r in run) else "success",
    }


def status(db: Session, settings: Settings) -> dict[str, Any]:
    """The last run of each table (scheduled or manual), the timer's last and next runs, and the latest manual
    sync."""
    tables = []
    for table in SOURCES:
        last = db.scalars(
            select(SyncRunLog)
            .where(SyncRunLog.sync_name == SYNC_NAME, SyncRunLog.table_name == table)
            .order_by(SyncRunLog.id.desc())
            .limit(1)
        ).first()
        tables.append({
            "table": table,
            "last_run_at": last.finished_at or last.started_at if last else None,
            "status": last.status if last else None,
            "row_count": last.row_count if last else None,
            "error": last.error_message if last else None,
        })  # fmt: skip
    latest = db.scalars(
        select(Job).where(Job.type == JobType.REFERENCE_SYNC.value).order_by(Job.id.desc()).limit(1)
    ).first()
    return {
        "configured": configured(settings),
        "schedule": SCHEDULE,
        "last_automatic": last_automatic(db),
        "next_automatic_at": next_scheduled(_now()),
        "tables": tables,
        "manual": _job_out(latest),
    }


def run_reference_sync_jobs(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: run a queued manual sync, if any."""
    now = _now()
    for stale in db.scalars(
        select(Job).where(
            Job.type == JobType.REFERENCE_SYNC.value,
            Job.status == JobStatus.RUNNING.value,
            Job.started_at < now - STALE_RUNNING,
        )  # fmt: skip
    ).all():
        stale.status, stale.error, stale.finished_at = (
            JobStatus.FAILED.value,
            "Interrupted; please try again.",
            now,
        )
    db.commit()
    job = db.scalars(
        select(Job)
        .where(Job.type == JobType.REFERENCE_SYNC.value, Job.status == JobStatus.QUEUED.value)
        .order_by(Job.id)
        .limit(1)  # fmt: skip
    ).first()
    if job is None:
        return {"done": 0, "failed": 0}
    job.status, job.started_at, job.attempts = JobStatus.RUNNING.value, _now(), job.attempts + 1
    db.commit()
    if not configured(settings):
        results: list[dict[str, Any]] = []
        job.error = "The Portal sync isn't set up on the server."
    else:
        assert settings.portallive_database_url and settings.reference_sync_database_url
        source = create_engine(settings.portallive_database_url, pool_pre_ping=True, future=True)
        target = create_engine(settings.reference_sync_database_url, pool_pre_ping=True, future=True)
        try:
            results = [r.__dict__ for r in sync_reference_tables(target, source_fetcher(source))]
        finally:
            source.dispose()
            target.dispose()
        failed = [r["table"] for r in results if r["status"] != "success"]
        job.error = (
            f"Failed: {', '.join(failed)}. Those tables keep their previous data."[:500] if failed else None
        )
    job.result = {"tables": results}
    job.status = JobStatus.FAILED.value if job.error else JobStatus.DONE.value
    job.finished_at = _now()
    db.commit()
    return {
        "done": int(job.status == JobStatus.DONE.value),
        "failed": int(job.status == JobStatus.FAILED.value),
    }
