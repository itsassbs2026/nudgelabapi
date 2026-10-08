"""Role Play trainings (docs/ROLEPLAY.md): tracks, practice scores, progress history and a log of every pass

Agent tables (the agent writes them; the API reads them):
  trainings.completion_type        + 'roleplay'
  training_progress                + track (which track a roleplay trainee is on)
  training_progress_history        a progress row kept when a new assignment reason starts the trainee fresh
  roleplay_attempts                one practice conversation each: track, level, the grader's score and debrief
  training_pass_log                every pass, first or repeat (Prime Portal shows the newest; this keeps them all)

Nothing existing changes meaning: every new column is nullable, every new table starts empty. The agent's login needs
INSERT on the three new tables and UPDATE on training_progress.track (deploy/db-grants-0015-roleplay.sql).

Revision ID: 0015_roleplay
Revises: 0014_dashboard_assignments
Create Date: 2026-10-07 22:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0015_roleplay'
down_revision: str | None = '0014_dashboard_assignments'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INT = sa.Integer().with_variant(mysql.INTEGER(unsigned=True), 'mariadb').with_variant(mysql.INTEGER(unsigned=True), 'mysql')
BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')
TYPES_OLD = "enum('quiz','walkthrough','acknowledgment')"
TYPES_NEW = "enum('quiz','walkthrough','acknowledgment','roleplay')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE trainings MODIFY completion_type {TYPES_NEW} NOT NULL DEFAULT 'quiz'")
    op.add_column('training_progress', sa.Column('track', sa.String(length=40), nullable=True))
    op.create_table('training_progress_history',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('uid', INT, nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=False),
    sa.Column('track', sa.String(length=40), nullable=True),
    sa.Column('version_id', INT, nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('topics_covered', sa.JSON(), nullable=True),
    sa.Column('walkthrough_finished_at', mysql.DATETIME(fsp=3), nullable=True),
    sa.Column('quiz_attempted', sa.Boolean(), nullable=True),
    sa.Column('correct_questions', sa.JSON(), nullable=True),
    sa.Column('sessions_count', INT, nullable=True),
    sa.Column('first_started_at', mysql.DATETIME(fsp=3), nullable=True),
    sa.Column('passed_at', mysql.DATETIME(fsp=3), nullable=True),
    sa.Column('last_session_id', sa.CHAR(length=36), nullable=True),
    sa.Column('archived_reason', sa.String(length=100), nullable=True),
    sa.Column('archived_at', mysql.DATETIME(fsp=3), server_default=sa.text('CURRENT_TIMESTAMP(3)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_progress_history_trainee', 'training_progress_history', ['uid', 'training_id'], unique=False)
    op.create_table('roleplay_attempts',
    sa.Column('attempt_id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=True),
    sa.Column('uid', INT, nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=False),
    sa.Column('version_id', INT, nullable=True),
    sa.Column('track', sa.String(length=40), nullable=False),
    sa.Column('tier', sa.Enum('beginner', 'stress'), nullable=False),
    sa.Column('persona', sa.String(length=100), nullable=True),
    sa.Column('score', mysql.TINYINT(unsigned=True), nullable=True),
    sa.Column('quick_pauses', mysql.SMALLINT(unsigned=True), server_default='0', nullable=False),
    sa.Column('strength', sa.String(length=500), nullable=True),
    sa.Column('gap', sa.String(length=500), nullable=True),
    sa.Column('tip', sa.String(length=500), nullable=True),
    sa.Column('for_five', sa.String(length=500), nullable=True),
    sa.Column('grader_model', sa.String(length=80), nullable=True),
    sa.Column('started_at', mysql.DATETIME(fsp=3), nullable=True),
    sa.Column('ended_at', mysql.DATETIME(fsp=3), nullable=True),
    sa.CheckConstraint('score IS NULL OR score BETWEEN 1 AND 5', name='ck_roleplay_score'),
    sa.PrimaryKeyConstraint('attempt_id')
    )
    op.create_index('ix_roleplay_attempts_trainee', 'roleplay_attempts', ['uid', 'training_id', 'track'], unique=False)
    op.create_index('ix_roleplay_attempts_session', 'roleplay_attempts', ['session_id'], unique=False)
    op.create_table('training_pass_log',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('uid', INT, nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=False),
    sa.Column('version_id', INT, nullable=True),
    sa.Column('track', sa.String(length=40), nullable=True),
    sa.Column('passed_at', mysql.DATETIME(fsp=3), nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=True),
    sa.Column('kind', sa.Enum('first', 'repeat'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pass_log_trainee', 'training_pass_log', ['uid', 'training_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pass_log_trainee', table_name='training_pass_log')
    op.drop_table('training_pass_log')
    op.drop_index('ix_roleplay_attempts_session', table_name='roleplay_attempts')
    op.drop_index('ix_roleplay_attempts_trainee', table_name='roleplay_attempts')
    op.drop_table('roleplay_attempts')
    op.drop_index('ix_progress_history_trainee', table_name='training_progress_history')
    op.drop_table('training_progress_history')
    op.drop_column('training_progress', 'track')
    op.execute(f"ALTER TABLE trainings MODIFY completion_type {TYPES_OLD} NOT NULL DEFAULT 'quiz'")
