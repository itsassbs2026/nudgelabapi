"""Per-employee rate limits for /app (docs/APP_HANDOFF.md §3).

Keyed by the pass's uid, not the client address: a store's Wi-Fi puts many employees behind one IP. Uses the
same in-memory storage as the login limits (app/auth/rate_limit.py), so each Uvicorn worker counts on its own.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends
from limits import parse

from app.auth.rate_limit import limiter
from app.config import Settings, get_settings
from app.mobile.passes import Employee, get_employee
from app.utils.errors import ApiError


def _limited(scope: str, limit_of: Callable[[Settings], str]) -> Callable[..., Employee]:
    def dependency(
        employee: Employee = Depends(get_employee), settings: Settings = Depends(get_settings)
    ) -> Employee:
        limit = limit_of(settings)
        if not limiter.limiter.hit(parse(limit), "app", scope, str(employee.uid)):
            message = "Too many requests. Please try again later."
            raise ApiError(429, "rate_limited", message, {"limit": limit})
        return employee

    return dependency


reader = _limited("read", lambda s: s.app_read_rate_limit)
session_starter = _limited("session", lambda s: s.app_session_rate_limit)
