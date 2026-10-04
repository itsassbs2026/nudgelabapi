"""content_uploads (SPEC 6.5) and the extract_upload job type (Phase 12)

  content_uploads   new API table: one row per uploaded source document and its extracted text
  jobs              the type check gains 'extract_upload' (the worker checks a file and extracts its text)

Nothing here touches the agent's tables.

Revision ID: 0008_content_uploads
Revises: 0007_trainer_persona
Create Date: 2026-10-04 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0008_content_uploads'
down_revision: str | None = '0007_trainer_persona'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')
JOB_TYPES_OLD = "type IN ('export', 'prepare_for_voice', 'create_vocabulary')"
JOB_TYPES_NEW = "type IN ('export', 'prepare_for_voice', 'create_vocabulary', 'extract_upload')"


def upgrade() -> None:
    op.create_table('content_uploads',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('training_id', sa.String(length=50), nullable=False),
    sa.Column('s3_key', sa.String(length=300), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('extracted_text', sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql').with_variant(mysql.MEDIUMTEXT(), 'mariadb'), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('uploaded_by', BIGINT, nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.Column('completed_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'processing', 'ready', 'rejected')", name='ck_content_uploads_status'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['dash_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_content_uploads_training', 'content_uploads', ['training_id', 'created_at'], unique=False)
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_NEW)


def downgrade() -> None:
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_OLD)
    op.drop_index('ix_content_uploads_training', table_name='content_uploads')
    op.drop_table('content_uploads')
