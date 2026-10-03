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
    default_timezone: str = "America/Chicago"
    log_level: str = "INFO"

    database_url: str
    # DDL-capable login for `alembic upgrade`, separate from the app's runtime login (SPEC §6.1).
    database_migration_url: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.PROD


@lru_cache
def get_settings() -> Settings:
    return Settings()  # database_url comes from the environment or .env
