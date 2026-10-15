"""What's new (2026-10-15): which announcements each dashboard user has seen

The dashboard shows a short tour of a new feature once per person (the first is Ask NudgeLab). The announcements
themselves ship with the dashboard; this table records who has seen which one and when, so it shows once per
person on any device. The API's login reads and adds rows (deploy/db-grants-0018-announcements.sql).

Revision ID: 0018_announcements_seen
Revises: 0017_ask_nudgelab
Create Date: 2026-10-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0018_announcements_seen"
down_revision: str | None = "0017_ask_nudgelab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)


def upgrade() -> None:
    op.create_table(
        "dash_announcements_seen",
        sa.Column("user_id", BIGINT, nullable=False),
        sa.Column("announcement", sa.String(length=64), nullable=False),
        sa.Column(
            "seen_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["dash_users.id"]),
        sa.PrimaryKeyConstraint("user_id", "announcement"),
    )


def downgrade() -> None:
    op.drop_table("dash_announcements_seen")
