"""jobs table (SPEC 6.5): XLSX exports now, training preparation in Stage 2

Revision ID: 0003_jobs
Revises: 0002_api_tables
Create Date: 2026-10-03 07:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0003_jobs'
down_revision: str | None = '0002_api_tables'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=True),
    sa.Column('version_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('input', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_by', BIGINT, nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.Column('started_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.Column('finished_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.CheckConstraint("type IN ('export', 'prepare_for_voice', 'create_vocabulary')", name='ck_jobs_type'),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name='ck_jobs_status'),
    sa.ForeignKeyConstraint(['created_by'], ['dash_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_jobs_status', 'jobs', ['status', 'created_at'], unique=False)
    op.create_index('ix_jobs_creator', 'jobs', ['created_by', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_jobs_creator', table_name='jobs')
    op.drop_index('ix_jobs_status', table_name='jobs')
    op.drop_table('jobs')
