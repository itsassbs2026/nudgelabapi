"""dash_action_codes + the 'reference_sync' job type (2026-10-06): an Admin's emailed-code "Sync from Portal"

One new table (one-time email codes for sensitive Admin actions) and the jobs type check gains
'reference_sync'. Only the API's own tables change; nothing the agent uses. The API's login gets SELECT, INSERT,
UPDATE on dash_action_codes (deploy/db-grants-0013-sync-button.sql).

Revision ID: 0013_manual_reference_sync
Revises: 0012_sync_run_log
Create Date: 2026-10-06 23:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0013_manual_reference_sync'
down_revision: str | None = '0012_sync_run_log'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')
JOB_TYPES_OLD = "type IN ('export', 'prepare_for_voice', 'create_vocabulary', 'extract_upload', 'publish_version')"
JOB_TYPES_NEW = ("type IN ('export', 'prepare_for_voice', 'create_vocabulary', 'extract_upload', 'publish_version', "
                 "'reference_sync')")


def upgrade() -> None:
    op.create_table('dash_action_codes',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('user_id', BIGINT, nullable=False),
    sa.Column('purpose', sa.String(length=32), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('used_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['dash_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_dash_action_codes_user', 'dash_action_codes', ['user_id', 'purpose', 'created_at'], unique=False)
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_NEW)


def downgrade() -> None:
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_OLD)
    op.drop_index('ix_dash_action_codes_user', table_name='dash_action_codes')
    op.drop_table('dash_action_codes')
