"""Publishing a version (SPEC 10.1, Phase 16): draft → in review → published → retired.

    submit       draft → in_review; the content is locked (only drafts are edited)
    send back    in_review → draft, with a note for the author
    publish      in_review (or retired: a rollback) → published, by a worker job:
                   1. the version's training_topics / training_questions rows (the reports read them), as the
                      agent writes them for versions published from files
                   2. its Amazon Transcribe vocabulary, `nudgelab-<training>-v<version_id>`, created and
                      waited for until READY; its name goes into the published content's
                      `training.stt_vocabulary`
                   3. in one transaction: the version published, the previous one retired, and the training's
                      `active_version_id` switched. New sessions get it from then on; trainees who started an
                      older version stay on it until they pass (the agent's choose_version)

Publishing needs no errors from the checks (10.4) and, for a version that has never been live, at least one
preview call on it, made after its last edit, in which the trainee spoke (10.5). A version without a
completion key publishes only when the trainer confirms that passes won't reach Wanaka or Portal.
"""

from __future__ import annotations

import copy
import time
from datetime import timedelta
from typing import Any

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import and_, exists, func, insert, or_, select, update
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.dashboard import DashUser, Job, JobStatus, JobType
from app.reference.agent_tables import (
    session_transcripts,
    training_questions,
    training_sessions,
    training_topics,
    training_versions,
    trainings,
)
from app.services import audit
from app.services.audit import AuditAction
from app.studio import service
from app.studio.prepare import job_view
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

PREVIEW_UID_BASE = 900000
# What publishing writes beyond the studio's own grants (deploy/db-grants-0010-publish.sql, tested).
PUBLISH_TRAINING_UPDATABLE = frozenset({"active_version_id"})
PUBLISH_VERSION_UPDATABLE = frozenset({"status", "published_at", "published_by", "notes"})
STALE_RUNNING = timedelta(minutes=30)
VOCABULARY_POLL_SECONDS = 5


class PublishError(Exception):
    """Publishing stopped; the message is for the trainer. Nothing was switched."""


def vocabulary_name(training_id: str, version_id: int) -> str:
    return f"nudgelab-{training_id.replace('_', '-')}-v{version_id}"


def _status(db: Session, row: Any) -> str:
    return service.version_status(row, service._training(db, row.training_id).active_version_id)


def latest_preview(db: Session, row: Any) -> dict[str, Any] | None:
    """The newest preview call on this version since its last edit in which the trainee said something."""
    s = training_sessions.c
    since = row.updated_at or row.created_at
    spoke = exists().where(
        and_(session_transcripts.c.session_id == s.session_id, session_transcripts.c.role == "trainee")
    )
    query = select(s.session_id, s.started_at, s.uid).where(
        s.version_id == row.version_id, s.client == "preview", spoke
    )
    if since is not None:
        query = query.where(s.started_at >= since)
    found = db.execute(query.order_by(s.started_at.desc()).limit(1)).first()
    if found is None:
        return None
    user = db.get(DashUser, int(found.uid) - PREVIEW_UID_BASE) if found.uid >= PREVIEW_UID_BASE else None
    return {
        "session_id": found.session_id,
        "started_at": found.started_at,
        "by": user.full_name if user else None,
    }


def readiness(db: Session, version_id: int) -> dict[str, Any]:
    """What stands between this version and publishing, for the editor's panel."""
    row = service._version(db, version_id)
    training = service._training(db, row.training_id)
    status = service.version_status(row, training.active_version_id)
    checks = (
        service.validate_version(db, version_id)
        if row.content is not None
        else {"errors": [], "warnings": []}
    )
    preview = latest_preview(db, row)
    needs_preview = status != "retired"
    blockers = []
    if row.content is None:
        blockers.append("The version has no content.")
    if checks["errors"]:
        blockers.append("Fix the errors the checks found.")
    if needs_preview and preview is None:
        blockers.append("Make a preview call on this version (after its last edit) and say something in it.")
    return {
        "version_id": version_id,
        "status": status,
        "errors": len(checks["errors"]),
        "warnings": len(checks["warnings"]),
        "preview": preview,
        "needs_preview": needs_preview,
        "completion_key": training.completion_key,
        "can_submit": status == "draft" and row.content is not None and not checks["errors"],
        "can_publish": status in ("in_review", "retired") and not blockers,
        "blockers": blockers,
        "publish_job": latest_job(db, version_id),
    }


def submit(db: Session, current: CurrentUser, version_id: int, ip: str | None) -> dict[str, Any]:
    row = service._version(db, version_id)
    if _status(db, row) != "draft":
        raise ApiError(409, "not_a_draft", "Only a draft can be submitted.")
    if row.content is None:
        raise ApiError(422, "no_content", "This version has no content yet.")
    if service.validate_version(db, version_id)["errors"]:
        raise ApiError(422, "has_errors", "Fix the errors the checks found before submitting.")
    v = training_versions.c
    done = db.execute(
        update(training_versions)
        .where(v.version_id == version_id, v.status == "draft")
        .values(status="in_review", notes=None)
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise ApiError(409, "not_a_draft", "Only a draft can be submitted.")
    audit.record(db, AuditAction.VERSION_SUBMITTED, actor_user_id=current.user.id, target_type="version",
                 target_id=version_id, details={"training_id": row.training_id}, ip=ip)  # fmt: skip
    db.commit()
    return service.version_summary(db, version_id)


def send_back(
    db: Session, current: CurrentUser, version_id: int, note: str, ip: str | None
) -> dict[str, Any]:
    row = service._version(db, version_id)
    if _status(db, row) != "in_review":
        raise ApiError(409, "not_in_review", "Only a version in review can be sent back.")
    if _running_job(db, row.training_id):
        raise ApiError(409, "publishing", "This training is being published right now.")
    v = training_versions.c
    db.execute(
        update(training_versions)
        .where(v.version_id == version_id, v.status == "in_review")
        .values(status="draft", notes=note.strip() or None)
    )
    audit.record(db, AuditAction.VERSION_SENT_BACK, actor_user_id=current.user.id, target_type="version",
                 target_id=version_id, details={"training_id": row.training_id}, ip=ip)  # fmt: skip
    db.commit()
    return service.version_summary(db, version_id)


def _running_job(db: Session, training_id: str) -> Job | None:
    return db.scalars(
        select(Job).where(
            Job.type == JobType.PUBLISH_VERSION.value,
            Job.training_id == training_id,
            Job.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)),
        )
    ).first()


def latest_job(db: Session, version_id: int) -> dict[str, Any] | None:
    job = db.scalars(
        select(Job)
        .where(Job.type == JobType.PUBLISH_VERSION.value, Job.version_id == version_id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    ).first()
    return job_view(job) if job else None


def start(
    db: Session, current: CurrentUser, version_id: int, *, no_completion_key_ok: bool, ip: str | None
) -> dict[str, Any]:
    ready = readiness(db, version_id)
    if ready["status"] not in ("in_review", "retired"):
        raise ApiError(409, "not_publishable", "Submit the draft for review first.")
    if ready["blockers"]:
        raise ApiError(422, "not_ready", " ".join(ready["blockers"]), {"blockers": ready["blockers"]})
    if not ready["completion_key"] and not no_completion_key_ok:
        raise ApiError(409, "no_completion_key",
                       "No completion key: passes won't be copied to Wanaka or Portal.")  # fmt: skip
    row = service._version(db, version_id)
    if _running_job(db, row.training_id):
        raise ApiError(409, "publishing", "This training is already being published.")
    job = Job(
        type=JobType.PUBLISH_VERSION.value,
        training_id=row.training_id,
        version_id=version_id,
        status=JobStatus.QUEUED.value,
        input={"rollback": ready["status"] == "retired"},
        created_by=current.user.id,
    )
    db.add(job)
    db.commit()
    return job_view(job)


# --- The worker job ------------------------------------------------------------------------------------


def _catalog_rows(db: Session, version_id: int, content: dict[str, Any]) -> tuple[int, int]:
    """The version's topic and question rows, unless it has them (a rollback, or published from files)."""
    have = db.execute(
        select(func.count()).select_from(training_topics).where(training_topics.c.version_id == version_id)
    ).scalar()
    if have:
        return 0, 0
    topics = [
        {"version_id": version_id, "topic_number": int(t["number"]), "title": str(t["title"]).strip()[:200]}
        for t in content.get("knowledge_base", {}).get("topics", [])
    ]
    questions = []
    quiz = content.get("quiz") or {"sections": {}, "questions": []}
    for q in quiz.get("questions", []):
        section = quiz["sections"].get(q["section"], {}).get("name", "")
        variants = q["variants"].items() if q.get("variants") else [("", q)]
        for variant, body in variants:
            questions.append(
                {
                    "version_id": version_id,
                    "question_number": int(q["number"]),
                    "location_variant": variant,
                    "section_code": str(q["section"])[:5],
                    "section_name": str(section)[:100],
                    "question_text": body["question"],
                    "options": body["options"],
                    "correct_option": body["correct"],
                }
            )
    if topics:
        db.execute(insert(training_topics), topics)
    if questions:
        db.execute(insert(training_questions), questions)
    return len(topics), len(questions)


def ensure_vocabulary(settings: Settings, name: str, phrases: list[str]) -> None:
    """Create the vocabulary if it doesn't exist, and wait until Transcribe says READY."""
    client = boto3.client("transcribe", region_name=settings.transcribe_region)
    try:
        try:
            state = client.get_vocabulary(VocabularyName=name)["VocabularyState"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("NotFoundException", "BadRequestException"):
                raise
            client.create_vocabulary(VocabularyName=name, LanguageCode="en-US", Phrases=phrases)
            state = "PENDING"
        deadline = time.monotonic() + settings.vocabulary_wait_seconds
        while state == "PENDING":
            if time.monotonic() > deadline:
                raise PublishError("The speech vocabulary took too long to get ready. Try publishing again.")
            time.sleep(VOCABULARY_POLL_SECONDS)
            found = client.get_vocabulary(VocabularyName=name)
            state = found["VocabularyState"]
        if state != "READY":
            reason = client.get_vocabulary(VocabularyName=name).get("FailureReason", "")
            raise PublishError(f"Amazon Transcribe refused the vocabulary: {reason}"[:400])
    except (BotoCoreError, ClientError) as exc:
        logger.warning("vocabulary_failed", name=name, error=type(exc).__name__)
        raise PublishError(
            "The speech vocabulary couldn't be created right now. Try publishing again."
        ) from exc


def _publish(db: Session, settings: Settings, job: Job) -> dict[str, Any]:
    version_id = int(job.version_id or 0)
    row = db.execute(select(training_versions).where(training_versions.c.version_id == version_id)).first()
    if row is None or row.content is None:
        raise PublishError("The version is gone or has no content.")
    status = _status(db, row)
    if status not in ("in_review", "retired"):
        raise PublishError(f"The version is {status.replace('_', ' ')} now, so it wasn't published.")
    content = copy.deepcopy(service._json(row.content))
    topics, questions = _catalog_rows(db, version_id, content)
    db.commit()  # the rows are harmless on their own; keep them if the vocabulary step fails

    phrases = [str(p) for p in (content.get("vocabulary") or [])]
    name = None
    if phrases:
        name = vocabulary_name(row.training_id, version_id)
        ensure_vocabulary(settings, name, phrases)
        content.setdefault("training", {})["stt_vocabulary"] = name

    v = training_versions.c
    active = service._training(db, row.training_id).active_version_id
    # The live version: status 'published', or no status if it was published from files before Phase 10.
    live = or_(v.status == "published", and_(v.status.is_(None), v.version_id == active))
    previous = list(
        db.execute(
            select(v.version_id).where(v.training_id == row.training_id, live, v.version_id != version_id)
        )
        .scalars()
        .all()
    )
    if previous:
        db.execute(update(training_versions).where(v.version_id.in_(previous)).values(status="retired"))
    unchanged = v.status == row.status if row.status else v.status.is_(None)
    done = db.execute(
        update(training_versions)
        .where(v.version_id == version_id, unchanged)
        .values(
            status="published",
            published_at=service._now(),
            published_by=job.created_by,
            notes=None,
            content=content,
        )  # fmt: skip
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise PublishError("The version changed while it was being published, so it wasn't.")
    db.execute(
        update(trainings)
        .where(trainings.c.training_id == row.training_id)
        .values(active_version_id=version_id)
    )
    audit.record(db, AuditAction.TRAINING_PUBLISHED, actor_user_id=job.created_by, target_type="version",
                 target_id=version_id, details={"training_id": row.training_id, "retired": list(previous),
                                                "rollback": bool(job.input.get("rollback"))})  # fmt: skip
    return {"topics": topics, "questions": questions, "vocabulary": name, "retired": list(previous)}


def run_publish_jobs(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: publish queued versions, one at a time."""
    now = service._now()
    for job in db.scalars(
        select(Job).where(
            Job.type == JobType.PUBLISH_VERSION.value,
            Job.status == JobStatus.RUNNING.value,
            Job.started_at < now - STALE_RUNNING,
        )
    ).all():
        job.status, job.error, job.finished_at = JobStatus.FAILED.value, "Interrupted; please try again.", now
    db.commit()
    queued = db.scalars(
        select(Job)
        .where(Job.type == JobType.PUBLISH_VERSION.value, Job.status == JobStatus.QUEUED.value)
        .order_by(Job.created_at, Job.id)
        .limit(1)
    ).first()
    if queued is None:
        return {"done": 0, "failed": 0}
    job = queued
    job.status, job.started_at, job.attempts = JobStatus.RUNNING.value, service._now(), job.attempts + 1
    db.commit()
    try:
        job.result = _publish(db, settings, job)
        job.status = JobStatus.DONE.value
    except PublishError as exc:
        db.rollback()
        job.status, job.error = JobStatus.FAILED.value, str(exc)[:500]
    except Exception:
        db.rollback()
        logger.exception("publish_failed", job_id=job.id)
        job.status, job.error = JobStatus.FAILED.value, "Publishing failed. Nothing was switched; try again."
    job.finished_at = service._now()
    db.commit()
    return {
        "done": int(job.status == JobStatus.DONE.value),
        "failed": int(job.status == JobStatus.FAILED.value),
    }
