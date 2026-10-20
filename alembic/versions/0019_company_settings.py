"""Company settings (2026-10-20): the company's name and wording as Admin settings, so one codebase serves Prime
Communications and Suncom Mobile (each installation on its own servers and database)

Seeds `dash_settings` with Prime's values, so Prime sees no change; another installation's Admin edits them on
Admin > Settings. No new table, so no grants (the API already reads and updates `dash_settings`).

Revision ID: 0019_company_settings
Revises: 0018_announcements_seen
Create Date: 2026-10-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_company_settings"
down_revision: str | None = "0018_announcements_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = [
    ("company.name", "Prime Communications", "The company's name, as the AI and the dashboard say it"),
    ("company.description", "an AT&T authorized retailer", "What the company is, in a few words (for the AI)"),
    ("company.app_name", "the Prime app", "Where employees start a training call, as the AI says it"),
    ("company.portal_name", "Prime Portal", "The company portal that passes are copied to"),
    ("company.email_domain", "primecomms.com", "The company's email domain (for examples on forms)"),
]


def upgrade() -> None:
    table = sa.table(
        "dash_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("type", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(table, [{"key": k, "value": v, "type": "text", "description": d} for k, v, d in SETTINGS])


def downgrade() -> None:
    op.execute("DELETE FROM dash_settings WHERE `key` LIKE 'company.%'")
