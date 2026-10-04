"""testers (SPEC 6.5, Phase 14): the agent's tester page signs testers in from this table, not testers.json

One new table. The agent's login gets SELECT on it (deploy/db-grants-0009-admin.sql); nothing else changes for
the agent until its web.py reads the table.

Revision ID: 0009_testers
Revises: 0008_content_uploads
Create Date: 2026-10-04 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0009_testers'
down_revision: str | None = '0008_content_uploads'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')


def upgrade() -> None:
    op.create_table('testers',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('uid', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('trainings', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', BIGINT, nullable=True),
    sa.Column('code_changed_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.Column('updated_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['dash_users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash'),
    sa.UniqueConstraint('uid')
    )


def downgrade() -> None:
    op.drop_table('testers')
