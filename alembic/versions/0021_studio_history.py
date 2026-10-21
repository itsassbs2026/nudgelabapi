"""Studio autosave and version history (2026-10-22): earlier saves of a draft, and the settings that shape them

`dash_version_history` keeps snapshots of a draft's content (app/studio/history.py). Settings (Admin > Settings):
`studio.autosave_seconds` (20; 0 = no autosave), `studio.history_keep` (20 saves per draft; 0 = no history) and
`studio.history_min_minutes` (10: at most one snapshot per this many minutes). The API's login gets SELECT, INSERT,
DELETE on the new table (deploy/db-grants-0021-studio-history.sql); until that's run, saving still works and
history is simply not recorded.

Revision ID: 0021_studio_history
Revises: 0020_studio_tidy
Create Date: 2026-10-22 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0021_studio_history"
down_revision: str | None = "0020_studio_tidy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)
SETTINGS = [
    ("studio.autosave_seconds", "20", "Seconds after you stop typing before the Studio saves a draft (0 = no autosave)"),
    ("studio.history_keep", "20", "Earlier saves kept per draft, to go back to (0 = no history)"),
    ("studio.history_min_minutes", "10", "At most one history save per this many minutes"),
]


def upgrade() -> None:
    op.create_table(
        "dash_version_history",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("saved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("saved_by", BIGINT, nullable=True),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["saved_by"], ["dash_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dash_version_history_version", "dash_version_history", ["version_id", "id"])
    table = sa.table(
        "dash_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("type", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(table, [{"key": k, "value": v, "type": "int", "description": d} for k, v, d in SETTINGS])


def downgrade() -> None:
    op.execute("DELETE FROM dash_settings WHERE `key` IN ('studio.autosave_seconds', 'studio.history_keep', 'studio.history_min_minutes')")
    op.drop_index("ix_dash_version_history_version", table_name="dash_version_history")
    op.drop_table("dash_version_history")
