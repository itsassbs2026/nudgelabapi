from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.dashboard import DashRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    timezone: str
    must_change_password: bool
    last_login_at: datetime | None
    locked_until: datetime | None = None
    created_at: datetime | None = None
    preferences: dict[str, Any] | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int
    user: UserOut


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=1, max_length=256)


class MeUpdate(BaseModel):
    """Users can change only their own display preferences and time zone."""

    timezone: str | None = Field(default=None, max_length=64)
    preferences: dict[str, Any] | None = None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    role: DashRole = DashRole.TRAINER
    timezone: str | None = Field(default=None, max_length=64)
    # Set one, or leave it out to email an invitation link instead.
    temporary_password: str | None = Field(default=None, max_length=256)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    role: DashRole | None = None
    is_active: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)


class AdminPasswordReset(BaseModel):
    """Leave temporary_password out to email the user a reset link instead."""

    temporary_password: str | None = Field(default=None, max_length=256)


class UserList(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int


class AuditEntryOut(BaseModel):
    id: int
    created_at: datetime
    action: str
    actor_user_id: int | None
    actor_email: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any] | None
    ip: str | None


class AuditList(BaseModel):
    items: list[AuditEntryOut]
    total: int
    page: int
    page_size: int
