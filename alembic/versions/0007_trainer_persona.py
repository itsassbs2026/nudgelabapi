"""Trainer persona: the name and voice a session's trainer used

  app_session_starts   + trainer_name, voice_id   what the API put in the session's token
  training_sessions    + trainer_name             what the agent used (written by the agent once it supports it;
                                                  NULL for sessions before that, which all used the content's name)

Nullable columns, added in place on MySQL 8. The agent's inserts name their columns and are unaffected until the
agent change that fills `trainer_name`.

Revision ID: 0007_trainer_persona
Revises: 0006_version_editing
Create Date: 2026-10-04 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007_trainer_persona'
down_revision: str | None = '0006_version_editing'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('app_session_starts', sa.Column('trainer_name', sa.String(length=40), nullable=True))
    op.add_column('app_session_starts', sa.Column('voice_id', sa.String(length=40), nullable=True))
    op.add_column('training_sessions', sa.Column('trainer_name', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('training_sessions', 'trainer_name')
    op.drop_column('app_session_starts', 'voice_id')
    op.drop_column('app_session_starts', 'trainer_name')
