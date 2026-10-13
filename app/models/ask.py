"""Ask NudgeLab (docs/ASK_NUDGELAB.md, 2026-10-13): conversations, every message with its tool calls and
cost, the like / dislike on answers, and `dash_settings`, the Admin-managed tunables (Admin > Settings).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime

LongText = Text().with_variant(MEDIUMTEXT(), "mysql", "mariadb")


class DashSetting(Base):
    """One tunable: key, value as text, its type (int, bool, decimal, text), what it means."""

    __tablename__ = "dash_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), onupdate=func.utc_timestamp(6), nullable=False
    )


class AskConversation(Base):
    __tablename__ = "ask_conversations"
    __table_args__ = (Index("ix_ask_conversations_user", "user_id", "updated_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # "deleted" by its owner
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class AskMessage(Base):
    """A question (role user) or an answer (role assistant) with the tool calls, tokens, cost and time
    behind it."""

    __tablename__ = "ask_messages"
    __table_args__ = (
        Index("ix_ask_messages_conversation", "conversation_id", "id"),
        Index("ix_ask_messages_user_day", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("ask_conversations.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(LongText, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class AskFeedback(Base):
    """A like (1) or dislike (-1) on an answer, one per person per answer, changeable."""

    __tablename__ = "ask_feedback"

    message_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("ask_messages.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_users.id"), primary_key=True)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), onupdate=func.utc_timestamp(6), nullable=False
    )
