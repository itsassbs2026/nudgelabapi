"""Opt-out (2026-11-10): a trainee may opt out of a training that has an opt-out statement, and gets credit

The agent keeps the opt-out like an acknowledgment, word for word with the session as evidence, in
training_acknowledgments; `kind` says which it is ('acknowledgment' for every existing row and for anything an older
agent writes). The statement itself is content (training.json "opt_out"), so nothing else changes in the database.

Grants: none. The agent's INSERT and the API's SELECT on training_acknowledgments are table-wide.

Revision ID: 0026_opt_out
Revises: 0025_recommendations
Create Date: 2026-11-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_opt_out"
down_revision: str | None = "0025_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_acknowledgments",
        sa.Column("kind", sa.String(length=20), server_default="acknowledgment", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("training_acknowledgments", "kind")
