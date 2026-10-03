from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.routers import health
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

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # Handlers narrow the exception type; Starlette types them as Exception (the documented pattern).
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health.router, prefix="/api/v1")
    return app


app = create_app()
