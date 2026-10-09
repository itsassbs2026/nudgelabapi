"""Daily summary email (2026-10-09): its recipients and a record of each day's send

Two new API tables: `dash_summary_recipients` (managed by Admins on Admin > Daily summary; seeded with the first
recipient) and `dash_summary_runs` (one row per report day, so a day is never sent twice). The API's login gets
access to both (deploy/db-grants-0016-daily-summary.sql).

Revision ID: 0016_daily_summary
Revises: 0015_roleplay
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0016_daily_summary"
down_revision: str | None = "0015_roleplay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)
FIRST_RECIPIENT = "bgupta@primecomms.com"


def upgrade() -> None:
    recipients = op.create_table(
        "dash_summary_recipients",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("added_by", BIGINT, nullable=True),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["dash_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "dash_summary_runs",
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("by_claude", sa.Boolean(), nullable=False),
        sa.Column(
            "sent_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("report_date"),
    )
    op.bulk_insert(recipients, [{"email": FIRST_RECIPIENT, "is_active": True}])


def downgrade() -> None:
    op.drop_table("dash_summary_runs")
    op.drop_table("dash_summary_recipients")
