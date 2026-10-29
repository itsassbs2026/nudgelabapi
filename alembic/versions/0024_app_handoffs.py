"""Training in the employee portal (2026-10-29): one-time handoff codes from the portal to the training page

The portal's server asks for a code when an employee taps Start (app/mobile/handoff.py); the training page redeems
it once, within about a minute, for the voice session. Only a SHA-256 of the code is stored.

Grants: deploy/db-grants-0024-app-handoffs.sql (API: insert, read and mark used).

Revision ID: 0024_app_handoffs
Revises: 0023_progress_reset
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0024_app_handoffs"
down_revision: str | None = "0023_progress_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)


def upgrade() -> None:
    op.create_table(
        "app_handoffs",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("training_id", sa.String(length=50), nullable=False),
        sa.Column("start_over", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("return_url", sa.String(length=500), nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_app_handoffs_code_hash"),
    )
    op.create_index("ix_app_handoffs_created", "app_handoffs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_app_handoffs_created", table_name="app_handoffs")
    op.drop_table("app_handoffs")
