"""agent_servers (2026-10-06): each agent server's capacity and last check-in, for the app's busy check

One new table. The agent's login gets SELECT, INSERT, UPDATE on it (deploy/db-grants-0011-agent-servers.sql):
every agent server writes its own row every 30 seconds.

Revision ID: 0011_agent_servers
Revises: 0010_publish_jobs
Create Date: 2026-10-06 02:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0011_agent_servers'
down_revision: str | None = '0010_publish_jobs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_servers',
    sa.Column('server_name', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('capacity', sa.SmallInteger(), nullable=False),
    sa.Column('accepting', sa.Boolean(), nullable=False),
    sa.Column('release_commit', sa.String(length=40), nullable=True),
    sa.Column('last_seen', mysql.DATETIME(fsp=6), nullable=False),
    sa.PrimaryKeyConstraint('server_name')
    )


def downgrade() -> None:
    op.drop_table('agent_servers')
