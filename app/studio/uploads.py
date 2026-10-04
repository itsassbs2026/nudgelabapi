"""Uploading a source document for a training (SPEC 8.3 uploads, §12.4; Phase 12).

1. `presign`: the browser gets a presigned POST for `<prefix>/pending/<random>` with the content type and a
   size range fixed by S3's policy. Nothing about the key comes from the file name.
2. The browser posts the file straight to S3; the API never handles the bytes in a request.
3. `complete`: the API confirms the object exists and is within the limit, and queues an `extract_upload` job.
4. The worker (`run_extract_jobs`) checks the file by its content, extracts the text, and moves it to
   `<prefix>/uploads/<upload id><ext>`, or rejects it with a message the trainer sees. Pending files that are
   never completed are deleted by the bucket's lifecycle rule after a day.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.content import ContentUpload, UploadStatus
from app.models.dashboard import DashUser, Job, JobStatus, JobType
from app.reference.agent_tables import trainings
from app.studio.extract import ALLOWED, EXTENSION, Rejected, extract
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)
STALE_RUNNING = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        region_name=settings.content_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _upload(db: Session, upload_id: int) -> ContentUpload:
    upload = db.get(ContentUpload, upload_id)
    if upload is None:
        raise ApiError(404, "not_found", "Upload not found.")
    return upload


def summary(db: Session, upload: ContentUpload) -> dict[str, Any]:
    name = db.get(DashUser, upload.uploaded_by).full_name if upload.uploaded_by else None  # type: ignore[union-attr]
    return {
        "upload_id": upload.id,
        "training_id": upload.training_id,
        "filename": upload.original_filename,
        "content_type": upload.content_type,
        "size_bytes": upload.size_bytes,
        "status": upload.status,
        "error": upload.error,
        "text_chars": len(upload.extracted_text) if upload.extracted_text else None,
        "uploaded_by": name,
        "created_at": upload.created_at,
        "completed_at": upload.completed_at,
    }


def presign(
    db: Session, settings: Settings, current: CurrentUser, training_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    if (
        db.execute(select(trainings.c.training_id).where(trainings.c.training_id == training_id)).first()
        is None
    ):
        raise ApiError(404, "not_found", "Training not found.")
    content_type, filename = data["content_type"], data["filename"]
    if not filename.lower().endswith(ALLOWED.get(content_type, ())):
        allowed = ", ".join(sorted(EXTENSION.values()))
        raise ApiError(422, "unsupported_file", f"Upload a {allowed} file whose name matches its type.")
    if data["size_bytes"] > settings.upload_max_bytes:
        raise ApiError(
            413, "file_too_large", f"Files can be up to {settings.upload_max_bytes // (1024 * 1024)} MB."
        )
    key = f"{settings.content_key_prefix}/pending/{uuid.uuid4().hex}"
    try:
        post = s3_client(settings).generate_presigned_post(
            Bucket=settings.content_bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, settings.upload_max_bytes],
            ],
            ExpiresIn=settings.upload_url_seconds,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("upload_presign_failed", error=type(exc).__name__)
        raise ApiError(503, "uploads_unavailable", "Uploads aren't available right now.") from exc
    upload = ContentUpload(
        training_id=training_id,
        s3_key=key,
        original_filename=filename,
        content_type=content_type,
        status=UploadStatus.PENDING.value,
        uploaded_by=current.user.id,
    )
    db.add(upload)
    db.commit()
    return {
        "upload_id": upload.id,
        "url": post["url"],
        "fields": post["fields"],
        "expires_in": settings.upload_url_seconds,
        "max_bytes": settings.upload_max_bytes,
    }


def complete(db: Session, settings: Settings, current: CurrentUser, upload_id: int) -> dict[str, Any]:
    upload = _upload(db, upload_id)
    if upload.uploaded_by != current.user.id:
        raise ApiError(403, "forbidden", "Only the person who uploaded this file can confirm it.")
    if upload.status != UploadStatus.PENDING.value:
        return summary(db, upload)  # confirming twice is harmless
    try:
        head = s3_client(settings).head_object(Bucket=settings.content_bucket, Key=upload.s3_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "403", "NoSuchKey"):
            raise ApiError(
                409, "not_uploaded", "The file hasn't arrived yet. Upload it, then try again."
            ) from exc
        raise ApiError(503, "uploads_unavailable", "Uploads aren't available right now.") from exc
    size = int(head.get("ContentLength", 0))
    if not 0 < size <= settings.upload_max_bytes:
        upload.status, upload.error, upload.completed_at = (
            UploadStatus.REJECTED.value,
            "The file is too large.",
            _now(),
        )
        db.commit()
        return summary(db, upload)
    upload.size_bytes, upload.status = size, UploadStatus.PROCESSING.value
    db.add(
        Job(
            type=JobType.EXTRACT_UPLOAD.value,
            training_id=upload.training_id,
            status=JobStatus.QUEUED.value,
            input={"upload_id": upload.id},
            created_by=current.user.id,
        )
    )
    db.commit()
    return summary(db, upload)


def get(db: Session, upload_id: int) -> dict[str, Any]:
    return summary(db, _upload(db, upload_id))


def text(db: Session, upload_id: int) -> dict[str, Any]:
    upload = _upload(db, upload_id)
    if upload.status != UploadStatus.READY.value:
        raise ApiError(409, "not_ready", "This document's text isn't ready.")
    return {"upload_id": upload.id, "filename": upload.original_filename, "text": upload.extracted_text}


def list_for_training(db: Session, training_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ContentUpload)
        .where(ContentUpload.training_id == training_id, ContentUpload.status != UploadStatus.PENDING.value)
        .order_by(ContentUpload.created_at.desc(), ContentUpload.id.desc())
    ).all()
    return [summary(db, u) for u in rows]


# --- Worker ----------------------------------------------------------------------------------------------


def _extract_one(db: Session, settings: Settings, upload: ContentUpload) -> None:
    s3 = s3_client(settings)
    body = s3.get_object(Bucket=settings.content_bucket, Key=upload.s3_key)["Body"]
    data = body.read(settings.upload_max_bytes + 1)
    if len(data) > settings.upload_max_bytes:
        raise Rejected("The file is too large.")
    upload.extracted_text = extract(data, upload.content_type, settings.extracted_text_max_chars)
    kept = f"{settings.content_key_prefix}/uploads/{upload.id}{EXTENSION[upload.content_type]}"
    s3.copy_object(
        Bucket=settings.content_bucket,
        Key=kept,
        CopySource={"Bucket": settings.content_bucket, "Key": upload.s3_key},
    )
    s3.delete_object(Bucket=settings.content_bucket, Key=upload.s3_key)
    upload.s3_key = kept
    upload.status, upload.error, upload.completed_at = UploadStatus.READY.value, None, _now()


def run_extract_jobs(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: check queued uploads and extract their text, oldest first."""
    now = _now()
    for job in db.scalars(
        select(Job).where(
            Job.type == JobType.EXTRACT_UPLOAD.value,
            Job.status == JobStatus.RUNNING.value,
            Job.started_at < now - STALE_RUNNING,
        )
    ).all():
        job.status, job.error, job.finished_at = JobStatus.QUEUED.value, None, None  # interrupted: run again
    db.commit()

    done = rejected = failed = 0
    queued = db.scalars(
        select(Job)
        .where(Job.type == JobType.EXTRACT_UPLOAD.value, Job.status == JobStatus.QUEUED.value)
        .order_by(Job.created_at, Job.id)
        .limit(10)
    ).all()
    for job in queued:
        upload = db.get(ContentUpload, int(job.input["upload_id"]))
        job.status, job.started_at, job.attempts = JobStatus.RUNNING.value, _now(), job.attempts + 1
        db.commit()
        if upload is None:
            job.status, job.error = JobStatus.FAILED.value, "The upload no longer exists."
            failed += 1
        else:
            try:
                _extract_one(db, settings, upload)
                job.status, job.result = (
                    JobStatus.DONE.value,
                    {"text_chars": len(upload.extracted_text or "")},
                )
                done += 1
            except Rejected as exc:
                upload.status, upload.error, upload.completed_at = (
                    UploadStatus.REJECTED.value,
                    str(exc),
                    _now(),
                )
                job.status, job.error = JobStatus.DONE.value, str(exc)[:500]
                _delete_quietly(settings, upload.s3_key)
                rejected += 1
            except Exception:
                logger.exception("upload_extract_failed", upload_id=upload.id)
                if job.attempts >= 3:
                    upload.status, upload.completed_at = UploadStatus.REJECTED.value, _now()
                    upload.error = "This file couldn't be processed. Try uploading it again."
                    job.status, job.error = JobStatus.FAILED.value, "Extraction failed."
                else:
                    job.status = JobStatus.QUEUED.value  # S3 or a network hiccup: try again next round
                failed += 1
        job.finished_at = _now() if job.status != JobStatus.QUEUED.value else None
        db.commit()
    return {"done": done, "rejected": rejected, "failed": failed}


def _delete_quietly(settings: Settings, key: str) -> None:
    try:
        s3_client(settings).delete_object(Bucket=settings.content_bucket, Key=key)
    except (BotoCoreError, ClientError):
        logger.warning("upload_delete_failed", key=key)
