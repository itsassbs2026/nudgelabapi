"""Content recommendations (2026-11-05): a nightly look at each training's last 7 days of calls

dash_recommendation_runs has one row per training per night (what was looked at, the items for the NudgeLab team);
dash_recommendations holds what trainers see in the Studio, with done / dismissed / replaced status.

Grants: deploy/db-grants-0025-recommendations.sql (API: read, insert, status updates).

Revision ID: 0025_recommendations
Revises: 0024_app_handoffs
Create Date: 2026-11-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0025_recommendations"
down_revision: str | None = "0024_app_handoffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)
NOW = sa.text("utc_timestamp(6)")


def upgrade() -> None:
    op.create_table(
        "dash_recommendation_runs",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("training_id", sa.String(length=50), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("window_from", sa.Date(), nullable=False),
        sa.Column("window_to", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("ours", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dash_recommendation_runs_training", "dash_recommendation_runs", ["training_id", "run_date"]
    )
    op.create_table(
        "dash_recommendations",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("run_id", BIGINT, nullable=False),
        sa.Column("training_id", sa.String(length=50), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("area", sa.String(length=20), nullable=False),
        sa.Column("target", sa.String(length=80), nullable=True),
        sa.Column("impact", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("status_note", sa.String(length=300), nullable=True),
        sa.Column("status_by", BIGINT, nullable=True),
        sa.Column("status_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dash_recommendation_runs.id"], name="fk_dash_recs_run"),
        sa.ForeignKeyConstraint(["status_by"], ["dash_users.id"], name="fk_dash_recs_status_by"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dash_recommendations_training", "dash_recommendations", ["training_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_dash_recommendations_training", table_name="dash_recommendations")
    op.drop_table("dash_recommendations")
    op.drop_index("ix_dash_recommendation_runs_training", table_name="dash_recommendation_runs")
    op.drop_table("dash_recommendation_runs")
