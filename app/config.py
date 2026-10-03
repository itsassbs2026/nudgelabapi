from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    ALPHA = "alpha"
    PROD = "prod"


class Settings(BaseSettings):
    """SPEC §14.3. Later phases add their settings here as they need them."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.ALPHA
    cors_origins: str = "http://localhost:5173"
    # Where the dashboard lives; used to build links in emails (password reset).
    dashboard_base_url: str = "http://localhost:5173"
    default_timezone: str = "America/Chicago"
    log_level: str = "INFO"

    database_url: str
    # DDL-capable login for `alembic upgrade`, separate from the app's runtime login (SPEC §6.1).
    database_migration_url: str | None = None

    # Auth (SPEC §9): HS256 access tokens held in memory by the SPA; rotating refresh tokens in a cookie.
    jwt_secret: str
    access_token_minutes: int = 15
    refresh_token_hours: int = 12
    refresh_token_max_days: int = 7
    password_reset_token_minutes: int = 60
    login_rate_limit: str = "10/minute"

    # First Admin (scripts/bootstrap_admin.py; refuses if an Admin exists).
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_full_name: str = "Binay Gupta"

    # Microsoft Graph for password-reset email (reusing pingit's app registration and sender mailbox).
    # Off until all values are set: the outbox fills up, and the worker sends once Graph is configured.
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: str | None = None
    graph_sender_mailbox: str | None = None
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_authority_host: str = "https://login.microsoftonline.com"
    graph_request_timeout_seconds: int = 30
    email_enabled: bool = False

    # Session recordings (SPEC §12). The agent's egress writes recordings/YYYY/MM/<session_id>.ogg; the
    # bucket's lifecycle rule deletes them after 90 days. Credentials come from boto3's default chain (the EC2
    # instance role), never from config.
    recordings_bucket: str = "nudgeailab"
    recordings_region: str = "us-west-1"
    recording_url_seconds: int = 300
    recording_retention_days: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.PROD

    @property
    def graph_configured(self) -> bool:
        return bool(
            self.email_enabled
            and self.graph_tenant_id
            and self.graph_client_id
            and self.graph_client_secret
            and self.graph_sender_mailbox
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # database_url and jwt_secret come from the environment or .env
