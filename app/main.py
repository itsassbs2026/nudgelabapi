from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.auth.rate_limit import limiter, rate_limit_exceeded_handler
from app.config import get_settings
from app.routers import admin_users, auth, health, me, reports, sessions
from app.utils.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.utils.logging import configure_logging
from app.utils.request_context import RequestIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # OpenAPI docs only outside prod; the dashboard generates its types from them (SPEC §3.2).
    app = FastAPI(
        title="NudgeLab API",
        version=__version__,
        docs_url=None if settings.is_prod else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/api/openapi.json",
    )

    app.state.limiter = limiter
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(SlowAPIMiddleware)

    # Handlers narrow the exception type; Starlette types them as Exception (the documented pattern).
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    for router in (
        health.router,
        auth.router,
        me.router,
        admin_users.router,
        reports.router,
        sessions.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
