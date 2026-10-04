"""Training management (SPEC 8.3, Phase 11): trainings, versions, content and diffs. Trainers and Admins.

Archiving a training and hiding it from the app are Admin-only (SPEC §4). Publishing is Phase 16: nothing here
changes which version the voice agent runs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.db import get_db
from app.routers.auth import client_ip
from app.schemas.studio import (
    TRAINING_ID_PATTERN,
    ContentSave,
    TrainingCreate,
    TrainingDetail,
    TrainingListItem,
    TrainingUpdate,
    VersionContent,
    VersionCreate,
    VersionDiff,
    VersionSummary,
)
from app.studio import service

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


@router.get("/versions/{from_id}/diff/{to_id}", response_model=VersionDiff)
def diff_versions(
    from_id: int = Path(ge=1),
    to_id: int = Path(ge=1),
    _: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.version_diff(db, from_id, to_id)
