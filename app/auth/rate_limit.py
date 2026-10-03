"""Rate limiting (SPEC §9): /auth/login is capped per client IP. Behind Nginx, Uvicorn's --proxy-headers makes
request.client the real client address."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings
from app.utils.errors import error_body

limiter = Limiter(key_func=get_remote_address)


def login_rate_limit() -> str:
    return get_settings().login_rate_limit


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_body(
            "rate_limited", "Too many requests. Please try again later.", {"limit": str(exc.detail)}
        ),
    )
