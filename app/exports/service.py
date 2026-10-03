"""Export requests, XLSX jobs and their files (SPEC §7.2, §12).

CSV is streamed straight back. XLSX is a job: the API queues it, the worker builds the file into EXPORT_DIR,
and the user downloads it (only the person who asked can). Files are deleted after EXPORT_KEEP_HOURS. Every
export is audit logged with its report, filters and row count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.exports.reports import REPORTS, NoParams, Report
from app.exports.writer import write_xlsx
from app.models.dashboard import DashRole, DashUser, Job, JobStatus, JobType
from app.reports.filters import ReportFilters, build_filters
from app.services import audit
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

# A job left "running" this long was interrupted (the worker restarted mid-export).
STALE_RUNNING = timedelta(minutes=30)


class ExportFilters(BaseModel):
    """The global filters (SPEC §7.1), as the report pages send them."""

    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    training_id: str | None = Field(default=None, max_length=50)
    completion_type: Literal["quiz", "walkthrough", "acknowledgment"] | None = None
    region_id: int | None = None
    market_id: int | None = None
    district_id: int | None = None
    store_id: str | None = Field(default=None, max_length=12)
    profile_id: str | None = Field(default=None, max_length=30)
    voice_id: str | None = Field(default=None, max_length=40)
    include_bots: bool = False


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: str = Field(max_length=40)
    format: Literal["csv", "xlsx"]
    filters: ExportFilters = Field(default_factory=ExportFilters)
    params: dict[str, Any] = Field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def resolve(req: ExportRequest, *, timezone: str, is_admin: bool) -> tuple[Report, ReportFilters, NoParams]:
    report = REPORTS.get(req.report)
    if report is None:
        raise ApiError(422, "unknown_report", "That report can't be exported.", {"reports": sorted(REPORTS)})
    if req.filters.include_bots and not is_admin:
        raise ApiError(403, "forbidden", "Only Admins can include test sessions.")
    try:
        params = report.params.model_validate(req.params)
    except ValidationError as exc:
        details = {
            "errors": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        }
        raise ApiError(422, "invalid_params", "Some export settings aren't valid.", details) from exc
    filters = build_filters(timezone=timezone, **req.filters.model_dump())
    return report, filters, params


def fetch_rows(
    db: Session, settings: Settings, report: Report, f: ReportFilters, params: NoParams
) -> list[dict[str, Any]]:
    rows = report.fetch(db, settings, f, params, settings.export_max_rows + 1)
    if len(rows) > settings.export_max_rows:
        raise ApiError(
            422,
            "export_too_large",
            f"Exports are limited to {settings.export_max_rows:,} rows. Narrow the filters and try again.",
        )
    return rows


def filename(report: Report, f: ReportFilters, fmt: str) -> str:
    return f"nudgelab-{report.name.replace('_', '-')}-{f.date_from}-to-{f.date_to}.{fmt}"


def record_export(
    db: Session,
    current: CurrentUser,
    req: ExportRequest,
    *,
    ip: str | None,
    rows: int | None,
    job_id: int | None,
) -> None:
    audit.record(
        db,
        audit.AuditAction.EXPORT,
        actor_user_id=current.user.id,
        target_type="report",
        target_id=req.report,
        details={
            "format": req.format,
            "filters": req.filters.model_dump(mode="json", exclude_defaults=True),
            "params": req.params,
            "rows": rows,
            "job_id": job_id,
        },
        ip=ip,
    )


def queue_xlsx(db: Session, current: CurrentUser, req: ExportRequest, *, ip: str | None) -> Job:
    job = Job(
        type=JobType.EXPORT.value,
        status=JobStatus.QUEUED.value,
        input={**req.model_dump(mode="json"), "timezone": current.user.timezone},
        attempts=0,
        created_by=current.user.id,
    )
    db.add(job)
    db.flush()
    record_export(db, current, req, ip=ip, rows=None, job_id=job.id)
    db.commit()
    return job


def job_out(job: Job) -> dict[str, Any]:
    result = job.result or {}
    return {
        "id": job.id,
        "status": job.status,
        "report": job.input.get("report"),
        "format": job.input.get("format"),
        "rows": result.get("rows"),
        "filename": result.get("filename"),
        "expired": bool(result.get("expired")),
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def own_export(db: Session, current: CurrentUser, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.type != JobType.EXPORT.value or job.created_by != current.user.id:
        raise ApiError(404, "not_found", "Export not found.")
    return job


def export_file(settings: Settings, job: Job) -> tuple[Path, str]:
    if job.status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
        raise ApiError(409, "export_not_ready", "The export is still being prepared.")
    if job.status == JobStatus.FAILED.value:
        raise ApiError(409, "export_failed", job.error or "The export failed.")
    result = job.result or {}
    path = Path(settings.export_dir) / f"{job.id}.xlsx"
    if result.get("expired") or not path.exists():
        raise ApiError(
            410, "export_expired", f"Export files are kept for {settings.export_keep_hours} hours."
        )
    return path, str(result.get("filename") or path.name)


# -- worker --------------------------------------------------------------------------------------------------


def _build(db: Session, settings: Settings, job: Job) -> dict[str, Any]:
    req = ExportRequest.model_validate({k: v for k, v in job.input.items() if k != "timezone"})
    timezone = str(job.input.get("timezone") or settings.default_timezone)
    creator = db.get(DashUser, job.created_by) if job.created_by else None
    if creator is None or not creator.is_active:
        raise ApiError(403, "forbidden", "The person who asked for this export can no longer sign in.")
    report, f, params = resolve(req, timezone=timezone, is_admin=creator.role == DashRole.ADMIN.value)
    rows = fetch_rows(db, settings, report, f, params)
    name = filename(report, f, "xlsx")
    directory = Path(settings.export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    about = {
        "Report": report.title,
        "From": f.date_from,
        "To": f.date_to,
        "Time zone": timezone,
        "Filters": req.filters.model_dump_json(exclude_defaults=True),
        "Settings": params.model_dump_json(exclude_defaults=True),
        "Rows": len(rows),
        "Exported by": creator.email,
        "Created (UTC)": _utcnow().strftime("%Y-%m-%d %H:%M"),
    }
    write_xlsx(directory / f"{job.id}.xlsx", report, rows, timezone, about)
    return {"rows": len(rows), "filename": name}


def run_export_jobs(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: build queued XLSX exports, oldest first. One worker runs, so no row locking is needed."""
    now = _utcnow()
    stale = db.scalars(
        select(Job).where(
            Job.type == JobType.EXPORT.value,
            Job.status == JobStatus.RUNNING.value,
            Job.started_at < now - STALE_RUNNING,
        )
    ).all()
    for job in stale:
        job.status, job.error, job.finished_at = JobStatus.FAILED.value, "Interrupted; please try again.", now
    db.commit()

    done = failed = 0
    queued = db.scalars(
        select(Job)
        .where(Job.type == JobType.EXPORT.value, Job.status == JobStatus.QUEUED.value)
        .order_by(Job.created_at, Job.id)
        .limit(5)
    ).all()
    for job in queued:
        job.status, job.started_at, job.attempts = JobStatus.RUNNING.value, _utcnow(), job.attempts + 1
        db.commit()
        try:
            job.result = _build(db, settings, job)
            job.status = JobStatus.DONE.value
            done += 1
        except ApiError as exc:
            db.rollback()
            job.status, job.error = JobStatus.FAILED.value, exc.message[:500]
            failed += 1
        except Exception:
            db.rollback()
            logger.exception("export_failed", job_id=job.id)
            job.status, job.error = JobStatus.FAILED.value, "Something went wrong building this export."
            failed += 1
        job.finished_at = _utcnow()
        db.commit()
    return {"done": done, "failed": failed, "interrupted": len(stale)}


def purge_old_exports(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: delete export files older than EXPORT_KEEP_HOURS and mark their jobs expired."""
    cutoff = _utcnow() - timedelta(hours=settings.export_keep_hours)
    jobs = db.scalars(
        select(Job).where(
            Job.type == JobType.EXPORT.value,
            Job.status == JobStatus.DONE.value,
            Job.finished_at < cutoff,
        )
    ).all()
    removed = 0
    for job in jobs:
        if (job.result or {}).get("expired"):
            continue
        path = Path(settings.export_dir) / f"{job.id}.xlsx"
        if path.exists():
            path.unlink()
            removed += 1
        job.result = {**(job.result or {}), "expired": True}
    db.commit()
    return {"removed": removed}
