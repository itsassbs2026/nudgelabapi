"""Training in the employee portal (2026-10-29): the one-time handoff from the portal to the training page.

The portal (a Drupal 7 module, docs/PORTAL_TRAINING.md) lists the employee's trainings by calling /app/v1 with
a NudgeLab pass it signs itself (issuer "portal", app/mobile/passes.py). When the employee taps Start, the
portal's server asks for a handoff: a random code that works once, for about a minute, for that employee and
that training. The browser goes to the training page with it (training.myprimeportal.com/?h=<code>), and the
page redeems it for the LiveKit session, exactly as the app starts one.

Why a code and not the pass itself: the pass stays on the portal's server, and a code that has been used (or
is a minute old) is worth nothing if it turns up in a browser history or a screenshot. Only a hash is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.mobile.passes import Employee
from app.models.app import AppHandoff
from app.reference.agent_tables import vw_app_profile
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def safe_return_url(settings: Settings, url: str | None) -> str | None:
    """The portal page to go back to, only on one of the portal's own addresses (never an open redirect)."""
    if not url:
        return None
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return url if parts.scheme == "https" and origin in settings.portal_origin_list else None


def create(
    db: Session,
    settings: Settings,
    employee: Employee,
    training_id: str,
    *,
    start_over: bool,
    return_url: str | None,
) -> dict[str, Any]:
    code = secrets.token_urlsafe(24)
    db.add(
        AppHandoff(
            code_hash=_hash(code),
            uid=employee.uid,
            training_id=training_id,
            start_over=start_over,
            return_url=safe_return_url(settings, return_url),
            expires_at=_now() + timedelta(seconds=settings.portal_handoff_seconds),
        )
    )
    db.commit()
    base = settings.training_page_url.rstrip("/")
    return {"code": code, "url": f"{base}/?h={code}", "expires_in": settings.portal_handoff_seconds}


def redeem(db: Session, code: str) -> tuple[Employee, AppHandoff]:
    """Uses the code up (it never works twice, even if the session then fails to start) and returns who it
    is for. Raises 410 `handoff_expired` for an unknown, used or expired code: the page sends them back."""
    row = db.execute(select(AppHandoff).where(AppHandoff.code_hash == _hash(code))).scalar_one_or_none()
    now = _now()
    if row is None or row.used_at is not None or row.expires_at < now:
        logger.info("handoff_rejected", known=row is not None, used=bool(row and row.used_at))
        raise ApiError(
            410, "handoff_expired", "This link has expired. Go back to the portal and tap Start again."
        )
    taken = db.execute(
        update(AppHandoff).where(AppHandoff.id == row.id, AppHandoff.used_at.is_(None)).values(used_at=now)
    )
    db.commit()
    if taken.rowcount != 1:  # type: ignore[attr-defined]  # redeemed by another request a moment ago
        raise ApiError(
            410, "handoff_expired", "This link has expired. Go back to the portal and tap Start again."
        )
    profile = db.execute(select(vw_app_profile).where(vw_app_profile.c.uid == row.uid)).mappings().first()
    if profile is None:
        raise ApiError(403, "inactive_employee", "This account can't use trainings.")
    return Employee(uid=row.uid, jti=None, profile=dict(profile), issuer="portal"), row
