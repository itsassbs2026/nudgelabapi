"""sync_run_log (2026-10-06): one row per table per run of the PortalLive reference-table sync

One new table. The sync's own login (`nudgelab_sync`) gets SELECT, INSERT on it and the API's login SELECT
(deploy/db-grants-0012-reference-sync.sql).

Revision ID: 0012_sync_run_log
Revises: 0011_agent_servers
Create Date: 2026-10-06 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0012_sync_run_log'
down_revision: str | None = '0011_agent_servers'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.create_table('sync_run_log',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('sync_name', sa.String(length=64), nullable=False),
    sa.Column('table_name', sa.String(length=64), nullable=False),
    sa.Column('started_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('finished_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sync_run_log_name_started', 'sync_run_log', ['sync_name', 'started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sync_run_log_name_started', table_name='sync_run_log')
    op.drop_table('sync_run_log')
