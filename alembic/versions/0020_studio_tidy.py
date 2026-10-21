"""Studio tidy-up (2026-10-22): the "untouched draft" hint's age as an Admin setting

`studio.stale_draft_days`: a draft nobody has edited for this many days gets an "Archive this?" hint in the
Studio (never archived automatically). Seeds `dash_settings` (Admin > Settings); no new table, so no grants.
Archiving a draft itself needs no schema change: it's a version status ('archived'), and the API's login
already may UPDATE `training_versions.status`.

Revision ID: 0020_studio_tidy
Revises: 0019_company_settings
Create Date: 2026-10-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_studio_tidy"
down_revision: str | None = "0019_company_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = sa.table(
        "dash_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("type", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "key": "studio.stale_draft_days",
                "value": "60",
                "type": "int",
                "description": "Days without an edit before a draft gets an \"Archive this?\" hint (0 = no hint)",
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM dash_settings WHERE `key` = 'studio.stale_draft_days'")
