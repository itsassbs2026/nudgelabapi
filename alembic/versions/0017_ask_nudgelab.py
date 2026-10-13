"""Ask NudgeLab (2026-10-13): the settings table, conversations, messages and answer feedback

`dash_settings` holds Admin-managed tunables (Admin > Settings), seeded with Ask NudgeLab's: on/off, the daily
question limit per person, the tool-call cap, the model and its prices. `ask_conversations`, `ask_messages` (every
question and answer with its tool calls, tokens, cost and time) and `ask_feedback` (like / dislike per answer).
The API's login gets access to all four (deploy/db-grants-0017-ask.sql). docs/ASK_NUDGELAB.md.

Revision ID: 0017_ask_nudgelab
Revises: 0016_daily_summary
Create Date: 2026-10-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0017_ask_nudgelab"
down_revision: str | None = "0016_daily_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mariadb")
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
)
LONGTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql").with_variant(mysql.MEDIUMTEXT(), "mariadb")
NOW = sa.text("utc_timestamp(6)")

SETTINGS = [
    ("ask.enabled", "true", "bool", "Ask NudgeLab is on for everyone"),
    (
        "ask.daily_limit",
        "50",
        "int",
        "Questions per person per day (midnight to midnight Central); 0 = no limit",
    ),
    ("ask.max_tool_calls", "8", "int", "Data lookups the assistant may make for one question"),
    ("ask.model", "us.anthropic.claude-sonnet-5-5", "text", "The Claude model on Bedrock"),
    (
        "ask.input_per_m",
        "3.00",
        "decimal",
        "Model price per million input tokens, in dollars (for the cost line)",
    ),
    ("ask.output_per_m", "15.00", "decimal", "Model price per million output tokens, in dollars"),
]


def upgrade() -> None:
    settings = op.create_table(
        "dash_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("updated_by", BIGINT, nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["dash_users.id"],
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.bulk_insert(settings, [{"key": k, "value": v, "type": t, "description": d} for k, v, t, d in SETTINGS])
    op.create_table(
        "ask_conversations",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("user_id", BIGINT, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["dash_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ask_conversations_user", "ask_conversations", ["user_id", "updated_at"], unique=False)
    op.create_table(
        "ask_messages",
        sa.Column("id", BIGINT, autoincrement=True, nullable=False),
        sa.Column("conversation_id", BIGINT, nullable=False),
        sa.Column("user_id", BIGINT, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", LONGTEXT, nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=10, scale=5), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ask_conversations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["dash_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ask_messages_conversation", "ask_messages", ["conversation_id", "id"], unique=False)
    op.create_index("ix_ask_messages_user_day", "ask_messages", ["user_id", "created_at"], unique=False)
    op.create_table(
        "ask_feedback",
        sa.Column("message_id", BIGINT, nullable=False),
        sa.Column("user_id", BIGINT, nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ask_messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["dash_users.id"],
        ),
        sa.PrimaryKeyConstraint("message_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("ask_feedback")
    op.drop_index("ix_ask_messages_user_day", table_name="ask_messages")
    op.drop_index("ix_ask_messages_conversation", table_name="ask_messages")
    op.drop_table("ask_messages")
    op.drop_index("ix_ask_conversations_user", table_name="ask_conversations")
    op.drop_table("ask_conversations")
    op.drop_table("dash_settings")
