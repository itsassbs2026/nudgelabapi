"""jobs: the type check gains 'publish_version' (SPEC 10.1, Phase 16)

Publishing a version is a worker job: it writes the version's topic and question rows, creates its Transcribe
vocabulary and waits for it, then switches the training's live version. Only the API's own `jobs` table
changes; nothing the agent uses.

Revision ID: 0010_publish_jobs
Revises: 0009_testers
Create Date: 2026-10-04 22:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0010_publish_jobs'
down_revision: str | None = '0009_testers'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_OLD = "type IN ('export', 'prepare_for_voice', 'create_vocabulary', 'extract_upload')"
JOB_TYPES_NEW = "type IN ('export', 'prepare_for_voice', 'create_vocabulary', 'extract_upload', 'publish_version')"


def upgrade() -> None:
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_NEW)


def downgrade() -> None:
    op.drop_constraint('ck_jobs_type', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_type', 'jobs', JOB_TYPES_OLD)
