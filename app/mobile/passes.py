"""The NudgeLab pass (docs/APP_HANDOFF.md §2): how the Flutter app proves who the employee is.

Wanaka signs a short-lived ES256 JWT with its private key (docs/WANAKA_NUDGE_TOKEN.md); this API holds only
the public keys, so nothing here can make a pass. The uid comes from the pass's `sub` and nowhere else: no app
endpoint takes a uid from the request.

Dashboard tokens (HS256, JWT_SECRET) never verify here (ES256 only, Wanaka's keys only), and passes never
verify as dashboard tokens (HS256 only, a different secret).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt as pyjwt
import structlog
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.reference.agent_tables import vw_app_profile
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

ALGORITHM = "ES256"
_UID = re.compile(r"^[1-9][0-9]{0,9}$")
_bearer_scheme = HTTPBearer(auto_error=False)


class PassError(Exception):
    """The pass is missing, malformed, expired or not signed by a known key."""


class PassesNotConfigured(Exception):
    """APP_PASS_PUBLIC_KEYS is missing, or a key file can't be read."""


@dataclass(frozen=True)
class AppPass:
    uid: int
    jti: str | None


@lru_cache
def _load_keys(spec: tuple[tuple[str, str], ...]) -> dict[str, ec.EllipticCurvePublicKey]:
    keys: dict[str, ec.EllipticCurvePublicKey] = {}
    for kid, path in spec:
        try:
            key = load_pem_public_key(Path(path).read_bytes())
        except (OSError, ValueError) as exc:
            raise PassesNotConfigured(f"key {kid!r}: {type(exc).__name__}") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise PassesNotConfigured(f"key {kid!r} is not an EC P-256 public key")
        keys[kid] = key
    return keys


def public_keys(settings: Settings) -> dict[str, ec.EllipticCurvePublicKey]:
    files = settings.app_pass_key_files
    if not files:
        raise PassesNotConfigured("APP_PASS_PUBLIC_KEYS is not set")
    return _load_keys(tuple(sorted(files.items())))


def verify_pass(token: str, settings: Settings) -> AppPass:
    keys = public_keys(settings)
    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as exc:
        raise PassError("not a JWT") from exc
    if header.get("alg") != ALGORITHM:
        raise PassError("wrong algorithm")
    key = keys.get(str(header.get("kid")))
    if key is None:
        raise PassError("unknown key id")
    try:
        claims: dict[str, Any] = pyjwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=settings.app_pass_audience,
            issuer=settings.app_pass_issuer,
            leeway=settings.app_pass_leeway_seconds,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except pyjwt.PyJWTError as exc:
        raise PassError(type(exc).__name__) from exc
    sub = claims["sub"]
    if not isinstance(sub, str) or not _UID.match(sub):
        raise PassError("sub is not a uid")
    if int(claims["exp"]) - int(claims["iat"]) > settings.app_pass_max_lifetime_seconds:
        raise PassError("lifetime too long")
    jti = claims.get("jti")
    return AppPass(uid=int(sub), jti=str(jti)[:64] if jti else None)


@dataclass(frozen=True)
class Employee:
    uid: int
    jti: str | None
    profile: dict[str, Any]


def get_employee(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Employee:
    """A valid pass for an active employee. Every /app endpoint depends on this."""
    if credentials is None:
        raise ApiError(401, "invalid_pass", "Missing or invalid NudgeLab pass.")
    try:
        app_pass = verify_pass(credentials.credentials, settings)
    except PassesNotConfigured as exc:
        logger.error("app_passes_not_configured", reason=str(exc))
        raise ApiError(503, "app_not_configured", "Trainings aren't available right now.") from exc
    except PassError as exc:
        logger.info("app_pass_rejected", reason=str(exc))
        raise ApiError(401, "invalid_pass", "Missing or invalid NudgeLab pass.") from exc
    row = db.execute(select(vw_app_profile).where(vw_app_profile.c.uid == app_pass.uid)).mappings().first()
    if row is None:
        logger.info("app_pass_inactive_employee", uid=app_pass.uid)
        raise ApiError(403, "inactive_employee", "This account can't use trainings.")
    return Employee(uid=app_pass.uid, jti=app_pass.jti, profile=dict(row))
