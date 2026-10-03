"""Admin: dashboard users and the audit log (SPEC §4, §7.2). Every route requires the Admin role."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, require_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.models.dashboard import DashRole
from app.routers.auth import client_ip
from app.schemas.auth import (
    AdminPasswordReset,
    AuditEntryOut,
    AuditList,
    UserCreate,
    UserList,
    UserOut,
    UserUpdate,
)
from app.services import audit_log
from app.services import users as users_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserList)
def list_users(
    search: str | None = Query(default=None, max_length=100),
    role: DashRole | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserList:
    result = users_service.list_users(
        db,
        search=search,
        role=role.value if role else None,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return UserList(
        items=[UserOut.model_validate(u) for u in result.items],
        total=result.total,
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    request: Request,
    body: UserCreate,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    user = users_service.create_user(
        db,
        settings,
        current.user,
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        timezone=body.timezone,
        temporary_password=body.temporary_password,
        ip=client_ip(request),
    )
    return UserOut.model_validate(user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> UserOut:
    return UserOut.model_validate(users_service.get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    request: Request,
    user_id: int,
    body: UserUpdate,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    user = users_service.update_user(db, current.user, user_id, changes, ip=client_ip(request))
    return UserOut.model_validate(user)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    request: Request,
    user_id: int,
    body: AdminPasswordReset,
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    users_service.admin_reset_password(
        db, settings, current.user, user_id, temporary_password=body.temporary_password, ip=client_ip(request)
    )


@router.get("/audit", response_model=AuditList)
def audit(
    action: str | None = Query(default=None, max_length=64),
    actor_user_id: int | None = None,
    target_type: str | None = Query(default=None, max_length=32),
    target_id: str | None = Query(default=None, max_length=64),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditList:
    result = audit_log.list_entries(
        db,
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return AuditList(
        items=[
            AuditEntryOut(
                id=r.entry.id,
                created_at=r.entry.created_at,
                action=r.entry.action,
                actor_user_id=r.entry.actor_user_id,
                actor_email=r.actor_email,
                target_type=r.entry.target_type,
                target_id=r.entry.target_id,
                details=r.entry.details,
                ip=r.entry.ip,
            )
            for r in result.items
        ],
        total=result.total,
        page=page,
        page_size=page_size,
    )
