"""Flutter app hand-off (docs/APP_HANDOFF.md, phase A1)

Additive only; nothing the agent reads or writes changes meaning:
  trainings             + the app catalog: app_title, category, tags, is_required, app_status, wanaka_trainer_id
  training_assignments  + matched_rule_group_id, matched_rule_name, ai_flag (NULL for hand-made rows)
  vw_app_profile        (new view) an active employee's profile and district manager, for the app's list header
  app_session_starts    (new API table) one row per LiveKit token handed to the app

New columns are nullable or have a default, so MySQL 8 adds them in place (ALGORITHM=INSTANT): the agent's
inserts, which name their columns, are unaffected. The view runs with its definer's rights (the migration login,
which can read v_users), so the API login reads only the view's columns, as with vw_trainees.

Revision ID: 0005_app_handoff
Revises: 0004_training_content
Create Date: 2026-10-03 23:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0005_app_handoff'
down_revision: str | None = '0004_training_content'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')

APP_PROFILE_VIEW = """
CREATE VIEW vw_app_profile AS
SELECT U.uid           AS uid,
       U.name          AS name,
       U.job_id        AS job_id,
       U.job_title     AS job_title,
       U.store_id      AS store_id,
       U.store_name    AS store_name,
       U.district_name AS district_name,
       U.market_name   AS market_name,
       U.region_name   AS region_name,
       DM.name         AS district_manager_name,
       DM.userpicture  AS district_manager_picture
FROM v_users U
LEFT JOIN v_users DM ON DM.uid = U.district_id
WHERE U.status = 1
"""


def upgrade() -> None:
    op.add_column('trainings', sa.Column('app_title', sa.String(length=200), nullable=True))
    op.add_column('trainings', sa.Column('category', sa.String(length=100), nullable=True))
    op.add_column('trainings', sa.Column('tags', sa.String(length=500), nullable=True))
    op.add_column('trainings', sa.Column('is_required', sa.Boolean(), server_default=sa.text('1'), nullable=False))
    op.add_column('trainings', sa.Column('app_status', sa.String(length=16), server_default='active', nullable=False))
    op.add_column('trainings', sa.Column('wanaka_trainer_id', sa.Integer(), nullable=True))

    op.add_column('training_assignments', sa.Column('matched_rule_group_id', BIGINT, nullable=True))
    op.add_column('training_assignments', sa.Column('matched_rule_name', sa.String(length=150), nullable=True))
    op.add_column('training_assignments', sa.Column('ai_flag', sa.String(length=50), nullable=True))

    op.execute(APP_PROFILE_VIEW)

    op.create_table('app_session_starts',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=False),
    sa.Column('room_name', sa.String(length=100), nullable=False),
    sa.Column('start_over', sa.Boolean(), nullable=False),
    sa.Column('pass_jti', sa.String(length=64), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_app_session_starts_uid', 'app_session_starts', ['uid', 'created_at'], unique=False)
    op.create_index('ix_app_session_starts_created', 'app_session_starts', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_app_session_starts_created', table_name='app_session_starts')
    op.drop_index('ix_app_session_starts_uid', table_name='app_session_starts')
    op.drop_table('app_session_starts')
    op.execute('DROP VIEW vw_app_profile')
    op.drop_column('training_assignments', 'ai_flag')
    op.drop_column('training_assignments', 'matched_rule_name')
    op.drop_column('training_assignments', 'matched_rule_group_id')
    op.drop_column('trainings', 'wanaka_trainer_id')
    op.drop_column('trainings', 'app_status')
    op.drop_column('trainings', 'is_required')
    op.drop_column('trainings', 'tags')
    op.drop_column('trainings', 'category')
    op.drop_column('trainings', 'app_title')
