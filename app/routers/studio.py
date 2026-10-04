"""Training management (SPEC 8.3, Phase 11): trainings, versions, content and diffs. Trainers and Admins.

Archiving a training and hiding it from the app are Admin-only (SPEC §4). Publishing is Phase 16: nothing here
changes which version the voice agent runs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.config import Settings, get_settings
from app.db import get_db
from app.models.dashboard import Job, JobType
from app.routers.auth import client_ip
from app.schemas.studio import (
    TRAINING_ID_PATTERN,
    ContentSave,
    JobView,
    TrainingCreate,
    TrainingDetail,
    TrainingListItem,
    TrainingUpdate,
    UploadPresignIn,
    UploadPresignOut,
    UploadSummary,
    UploadText,
    ValidationOut,
    VersionContent,
    VersionCreate,
    VersionDiff,
    VersionSummary,
)
from app.services import audit
from app.services.audit import AuditAction
from app.studio import prepare, service, uploads
from app.utils.errors import ApiError

router = APIRouter(tags=["studio"])

TrainingId = Path(pattern=TRAINING_ID_PATTERN)
VersionId = Path(ge=1)


@router.get("/trainings", response_model=list[TrainingListItem])
def list_trainings(_: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    return service.list_trainings(db)


@router.post("/trainings", response_model=TrainingDetail, status_code=status.HTTP_201_CREATED)
def create_training(
    body: TrainingCreate,
    request: Request,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.create_training(db, current, body.model_dump(), client_ip(request))


@router.get("/trainings/{training_id}", response_model=TrainingDetail)
def get_training(
    training_id: str = TrainingId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return service.training_detail(db, training_id)


@router.patch("/trainings/{training_id}", response_model=TrainingDetail)
def update_training(
    body: TrainingUpdate,
    request: Request,
    training_id: str = TrainingId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.update_training(db, current, training_id, body.changes(), client_ip(request))


@router.get("/trainings/{training_id}/versions", response_model=list[VersionSummary])
def list_versions(
    training_id: str = TrainingId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return service.list_versions(db, training_id)


@router.post(
    "/trainings/{training_id}/versions", response_model=VersionSummary, status_code=status.HTTP_201_CREATED
)
def create_version(
    body: VersionCreate,
    request: Request,
    training_id: str = TrainingId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.create_version(db, current, training_id, body.model_dump(), client_ip(request))


@router.get("/versions/{version_id}/content", response_model=VersionContent)
def get_content(
    version_id: int = VersionId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return service.get_content(db, version_id)


@router.put("/versions/{version_id}/content", response_model=VersionSummary)
def save_content(
    body: ContentSave,
    version_id: int = VersionId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.save_content(db, current, version_id, body.revision, body.content)


@router.post("/versions/{version_id}/validate", response_model=ValidationOut)
def validate_version(
    version_id: int = VersionId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    """The publish checks (SPEC 10.4) on the version as saved. Errors block publishing; warnings don't."""
    return service.validate_version(db, version_id)


@router.get("/versions/{from_id}/diff/{to_id}", response_model=VersionDiff)
def diff_versions(
    from_id: int = Path(ge=1),
    to_id: int = Path(ge=1),
    _: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.version_diff(db, from_id, to_id)


# --- Uploads and prepare for voice (Phase 12) ------------------------------------------------------------


@router.post("/trainings/{training_id}/uploads/presign", response_model=UploadPresignOut)
def presign_upload(
    body: UploadPresignIn,
    request: Request,
    training_id: str = TrainingId,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    out = uploads.presign(db, settings, current, training_id, body.model_dump())
    audit.record(
        db,
        AuditAction.UPLOAD_STARTED,
        actor_user_id=current.user.id,
        target_type="training",
        target_id=training_id,
        details={"upload_id": out["upload_id"], "filename": body.filename, "size_bytes": body.size_bytes},
        ip=client_ip(request),
    )
    db.commit()
    return out


@router.post("/uploads/{upload_id}/complete", response_model=UploadSummary)
def complete_upload(
    upload_id: int = Path(ge=1),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Any:
    return uploads.complete(db, settings, current, upload_id)


@router.get("/uploads/{upload_id}", response_model=UploadSummary)
def get_upload(
    upload_id: int = Path(ge=1), _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return uploads.get(db, upload_id)


@router.get("/uploads/{upload_id}/text", response_model=UploadText)
def get_upload_text(
    upload_id: int = Path(ge=1), _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return uploads.text(db, upload_id)


@router.get("/trainings/{training_id}/uploads", response_model=list[UploadSummary])
def list_uploads(
    training_id: str = TrainingId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return uploads.list_for_training(db, training_id)


@router.post("/versions/{version_id}/prepare", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def start_prepare(
    version_id: int = VersionId, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    """Prepare (again) a draft made from a document. Overwrites the draft's content when done."""
    return prepare.job_view(prepare.queue(db, current, version_id))


@router.get("/versions/{version_id}/prepare", response_model=JobView)
def latest_prepare(
    version_id: int = VersionId, _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    view = prepare.latest_for_version(db, version_id)
    if view is None:
        raise ApiError(404, "not_found", "This draft hasn't been prepared.")
    return view


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(
    job_id: int = Path(ge=1), _: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    """Training studio jobs (preparing, extracting). Exports have their own /exports/{id}."""
    job = db.get(Job, job_id)
    if job is None or job.type not in (JobType.PREPARE_FOR_VOICE.value, JobType.EXTRACT_UPLOAD.value):
        raise ApiError(404, "not_found", "Job not found.")
    return prepare.job_view(job)
