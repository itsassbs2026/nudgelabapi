from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, func
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# BIGINT UNSIGNED on MySQL/MariaDB, plain BIGINT elsewhere (SPEC §6.3 conventions).
BigIntId = BigInteger().with_variant(BIGINT(unsigned=True), "mysql", "mariadb")
UtcDateTime = MySQLDateTime(fsp=6)


class Base(DeclarativeBase):
    """Only the API's own tables (SPEC §6.3) live on this metadata. The agent's tables are created by the
    baseline migration and are never part of autogenerate (alembic/env.py)."""


class TimestampMixin:
    """created_at / updated_at, DATETIME(6) UTC."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), onupdate=func.utc_timestamp(6), nullable=False
    )
