"""dash_permissions + who made an assignment (2026-10-07): assigning training from the dashboard

One new API table, `dash_permissions` (which dashboard actions each role may take; Admins always may), seeded
with the Trainer defaults. `training_assignments` gains two nullable columns, so the owner's database query and
the dashboard can share the table: `assigned_via` ('dashboard' or 'upload'; NULL for rows the query made) and
`assigned_by_user_id` (the dashboard user). The agent never reads either. The API's login gets INSERT, and
UPDATE on a few columns, on training_assignments (deploy/db-grants-0014-assignments.sql).

Revision ID: 0014_dashboard_assignments
Revises: 0013_manual_reference_sync
Create Date: 2026-10-07 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = '0014_dashboard_assignments'
down_revision: str | None = '0013_manual_reference_sync'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), 'mariadb').with_variant(mysql.BIGINT(unsigned=True), 'mysql')
TRAINER_DEFAULTS = [
    ('assign_single', True),
    ('assign_bulk', False),
    ('assign_cancel', True),
    ('assign_due_date', True),
]


def upgrade() -> None:
    permissions = op.create_table('dash_permissions',
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('updated_at', mysql.DATETIME(fsp=6), server_default=sa.text('utc_timestamp(6)'), nullable=False),
    sa.Column('updated_by', BIGINT, nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['dash_users.id'], ),
    sa.PrimaryKeyConstraint('action', 'role')
    )
    op.bulk_insert(permissions, [{'action': a, 'role': 'trainer', 'enabled': on} for a, on in TRAINER_DEFAULTS])
    op.add_column('training_assignments', sa.Column('assigned_via', sa.String(length=16), nullable=True))
    op.add_column('training_assignments', sa.Column('assigned_by_user_id', BIGINT, nullable=True))


def downgrade() -> None:
    op.drop_column('training_assignments', 'assigned_by_user_id')
    op.drop_column('training_assignments', 'assigned_via')
    op.drop_table('dash_permissions')
