"""Who is calling (SPEC §4, §9).

- `get_current_user`: a valid access token for an active user. The role comes from the token (changes apply at
  the next refresh, within 15 minutes); `is_active` is checked against the database on every request, so a
  deactivated user is locked out at once.
- `get_user`: the same, plus the user has no pending forced password change. Every endpoint uses this except
  "who am I", "change password" and "log out", so a temporary password can't be used for anything else.
- `require_admin`: `get_user` and the Admin role.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.jwt import AccessTokenError, decode_access_token
from app.config import Settings, get_settings
from app.db import get_db
from app.models.dashboard import DashRole, DashUser
from app.utils.errors import ApiError

_bearer_scheme = HTTPBearer(auto_error=False)


def _invalid_token() -> ApiError:
    return ApiError(401, "invalid_token", "Missing or invalid access token.")


@dataclass(frozen=True)
class CurrentUser:
    user: DashUser
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == DashRole.ADMIN


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if credentials is None:
        raise _invalid_token()
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except AccessTokenError as exc:
        raise _invalid_token() from exc
    user = db.get(DashUser, claims.user_id)
    if user is None or not user.is_active:
        raise _invalid_token()
    return CurrentUser(user=user, role=claims.role)


def get_user(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current.user.must_change_password:
        raise ApiError(403, "password_change_required", "Change your password before continuing.")
    return current


def require_admin(current: CurrentUser = Depends(get_user)) -> CurrentUser:
    if not current.is_admin:
        raise ApiError(403, "forbidden", "You do not have permission to do this.")
    return current
