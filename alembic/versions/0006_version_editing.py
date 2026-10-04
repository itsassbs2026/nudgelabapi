"""training_versions: editing a draft safely (SPEC 8.3, Phase 11)

Four additive columns on the agent's training_versions, nothing else:
  revision    bumped on every content save; a save must name the revision it started from, so two editors can't
              overwrite each other silently (optimistic locking)
  created_at  when the version row was made in the dashboard (NULL for versions published from files: their
              `published_at` is that time)
  updated_at  last content save
  updated_by  the dashboard user who saved last

Nullable or defaulted, so MySQL 8 adds them in place; the agent's inserts name their columns and are unaffected.
The agent never reads these columns.

Revision ID: 0006_version_editing
Revises: 0005_app_handoff
Create Date: 2026-10-04 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0006_version_editing'
down_revision: str | None = '0005_app_handoff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.add_column('training_versions', sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('training_versions', sa.Column('created_at', mysql.DATETIME(fsp=6), nullable=True))
    op.add_column('training_versions', sa.Column('updated_at', mysql.DATETIME(fsp=6), nullable=True))
    op.add_column('training_versions', sa.Column('updated_by', BIGINT, nullable=True))


def downgrade() -> None:
    op.drop_column('training_versions', 'updated_by')
    op.drop_column('training_versions', 'updated_at')
    op.drop_column('training_versions', 'created_at')
    op.drop_column('training_versions', 'revision')
