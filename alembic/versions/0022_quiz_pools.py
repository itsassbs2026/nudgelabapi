"""Question pools (2026-10-21): a quiz section can ask N of a bigger bank, drawn per trainee

`training_progress.quiz_draw` keeps what a pool dealt each trainee (the agent writes it; agent repo quiz_pool.py), so
a new call carries on with the same questions. The agent's login already has table-level UPDATE on
training_progress, so no new grant. The content setting is `quiz.sections.<code>.draw` (an Admin or trainer sets
it in the Studio); the checks live in app/studio/validate.py. Until this is applied the agent still runs pools but
can't remember a draw between calls (it logs the failed write, and an answer is never lost).

Revision ID: 0022_quiz_pools
Revises: 0021_studio_history
Create Date: 2026-10-21 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_quiz_pools"
down_revision: str | None = "0021_studio_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("training_progress", sa.Column("quiz_draw", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_progress", "quiz_draw")
