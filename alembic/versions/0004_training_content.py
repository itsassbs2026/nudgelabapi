"""training_versions: content and workflow columns (SPEC 6.5, Phase 10)

Adds four nullable columns to the agent's training_versions table, nothing else:
  status            draft | in_review | published | retired (NULL: a version published from files before Phase 10)
  content           the training as one JSON document (app/schemas/training_content.py; the agent's content.py)
  created_by        the dashboard user who made the version (NULL for versions published from files)
  source_upload_id  the uploaded document it was prepared from (Stage 2, Phase 12)

Nullable columns are added in place on MySQL 8 (ALGORITHM=INSTANT): no table copy, no lock the agent would
notice. Existing rows and the running agent are unaffected; the agent starts reading `content` once a version
has some. No foreign keys: the agent's tables stay independent of the API's (status is checked by the API).

Revision ID: 0004_training_content
Revises: 0003_jobs
Create Date: 2026-10-03 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0004_training_content'
down_revision: str | None = '0003_jobs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.add_column('training_versions', sa.Column('status', sa.String(length=16), nullable=True))
    op.add_column('training_versions', sa.Column('content', sa.JSON(), nullable=True))
    op.add_column('training_versions', sa.Column('created_by', BIGINT, nullable=True))
    op.add_column('training_versions', sa.Column('source_upload_id', BIGINT, nullable=True))


def downgrade() -> None:
    op.drop_column('training_versions', 'source_upload_id')
    op.drop_column('training_versions', 'created_by')
    op.drop_column('training_versions', 'content')
    op.drop_column('training_versions', 'status')
