"""Reset a person's training progress (2026-10-22): the queue that clears Prime Portal, and the permission

An Admin or Trainer can reset one person's progress on one training or all of them (app/services/progress_reset.py).
The progress is archived first (training_progress_history) and the person starts the training brand new. If they had
passed, Prime Portal's completion record has to go too, but only the agent servers hold a Portal write login, so
the API queues the request in `portal_clears` and the agent clears it within a minute (agent repo completions.py).
Trainers may reset by default (owner, 2026-10-22); Admins always may, and an Admin can switch it off on
Admin > Permissions.

Grants: deploy/db-grants-0023-progress-reset.sql (API: update progress, insert history and clears; agent: read and
update the clears). Portal's own login for the agent needs DELETE on the completions table (the owner, in Portal).

Revision ID: 0023_progress_reset
Revises: 0022_quiz_pools
Create Date: 2026-10-22 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0023_progress_reset"
down_revision: str | None = "0022_quiz_pools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)


def upgrade() -> None:
    op.create_table(
        "portal_clears",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("training_id", sa.String(length=50), nullable=False),
        sa.Column("completion_key", sa.String(length=64), nullable=False),
        sa.Column("requested_by", BIGINT, nullable=True),
        sa.Column(
            "requested_at", mysql.DATETIME(fsp=6), server_default=sa.text("utc_timestamp(6)"), nullable=False
        ),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("done_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["dash_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portal_clears_status", "portal_clears", ["status", "id"])
    permissions = sa.table(
        "dash_permissions",
        sa.column("action", sa.String),
        sa.column("role", sa.String),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(permissions, [{"action": "reset_progress", "role": "trainer", "enabled": True}])


def downgrade() -> None:
    op.execute("DELETE FROM dash_permissions WHERE action = 'reset_progress'")
    op.drop_index("ix_portal_clears_status", table_name="portal_clears")
    op.drop_table("portal_clears")
