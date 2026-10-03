"""Access tokens (SPEC §9): JWT HS256, 15 minutes, claims sub, role, iat, exp, jti.
The dashboard holds them in memory only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from app.config import Settings
from app.models.dashboard import DashUser

ALGORITHM = "HS256"


class AccessTokenError(Exception):
    """Any invalid or expired access token. Callers answer 401."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    role: str
    jti: str


def create_access_token(user: DashUser, settings: Settings) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": uuid.uuid4().hex,
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), settings.access_token_minutes * 60


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except pyjwt.PyJWTError as exc:
        raise AccessTokenError(str(exc)) from exc
    try:
        return AccessTokenClaims(
            user_id=int(payload["sub"]), role=str(payload["role"]), jti=str(payload["jti"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AccessTokenError("malformed token payload") from exc
