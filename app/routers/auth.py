"""SPEC §8.1 auth routes, under /api/v1/auth. Login, refresh, forgot and reset need no access token (refresh
uses its cookie); logout and change-password need one."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_current_user
from app.auth.rate_limit import limiter, login_rate_limit
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    ResetPasswordRequest,
    UserOut,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    # Host-only cookie (no Domain), so it's sent to the API host alone (see docs/DECISIONS.md).
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_hours * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.is_prod,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.is_prod,
        samesite="strict",
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit(login_rate_limit)
def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    result = auth_service.login(
        db,
        settings,
        body.email,
        body.password,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    _set_refresh_cookie(response, result.refresh_token, settings)
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=UserOut.model_validate(result.user),
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RefreshResponse:
    try:
        result = auth_service.refresh(
            db, settings, refresh_token, user_agent=request.headers.get("user-agent"), ip=client_ip(request)
        )
    except Exception:
        _clear_refresh_cookie(response, settings)
        raise
    _set_refresh_cookie(response, result.refresh_token, settings)
    return RefreshResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    current: CurrentUser = Depends(get_current_user),
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    auth_service.logout(db, current.user, refresh_token, ip=client_ip(request))
    _clear_refresh_cookie(response, settings)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Allowed while a password change is pending (that's how a temporary password gets replaced)."""
    auth_service.change_password(
        db, current.user, body.current_password, body.new_password, ip=client_ip(request)
    )


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(login_rate_limit)
def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    """Always 204, whether or not the email has an account."""
    auth_service.forgot_password(db, settings, body.email, ip=client_ip(request))


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(login_rate_limit)
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)) -> None:
    auth_service.reset_password(db, body.token, body.new_password, ip=client_ip(request))
