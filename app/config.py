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

    # Live sessions on the Overview (SPEC §7.2): LiveKit's room list, read-only. Off until all three are set.
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None

    # The Flutter app's training endpoints (/app/v1, docs/APP_HANDOFF.md). The app brings a NudgeLab pass: an
    # ES256 JWT signed by Wanaka (docs/WANAKA_NUDGE_TOKEN.md). Only the public keys live here, as
    # "kid=/path/key.pem", comma-separated so a rotation can list the old and new keys. Off (503) until set.
    app_pass_public_keys: str | None = None
    app_pass_issuer: str = "wanaka"
    app_pass_audience: str = "nudgelabapi"
    app_pass_max_lifetime_seconds: int = 3600
    app_pass_leeway_seconds: int = 30
    # Per employee (uid), not per IP: a store's Wi-Fi puts many people behind one address.
    app_read_rate_limit: str = "60/minute"
    app_session_rate_limit: str = "6/minute"
    # The voice agent's LiveKit name (the agent repo's web.py AGENT_NAME) and how long a session token can be
    # used to join or rejoin after a dropped connection (as the tester page).
    livekit_agent_name: str = "nudgelab-trainer"
    app_session_token_minutes: int = 30

    # Exports (SPEC §7.2): CSV streams straight back; XLSX is built by the worker into EXPORT_DIR (on the API
    # server's own disk, shared by the API and the worker) and deleted after EXPORT_KEEP_HOURS.
    export_dir: str = "var/exports"
    export_max_rows: int = 100_000
    export_keep_hours: int = 24

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def livekit_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def app_pass_key_files(self) -> dict[str, str]:
        """kid → public key file, from APP_PASS_PUBLIC_KEYS ("kid=/path.pem,kid2=/path2.pem")."""
        out: dict[str, str] = {}
        for item in (self.app_pass_public_keys or "").split(","):
            kid, sep, path = item.strip().partition("=")
            if sep and kid.strip() and path.strip():
                out[kid.strip()] = path.strip()
        return out

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
