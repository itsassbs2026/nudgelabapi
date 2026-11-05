"""Content recommendations (2026-11-05): what trainers could change in a training, from its recent calls.

A nightly job (scripts/recommendations.py) looks at each live training's calls and has Claude write a few
recommendations a trainer can act on in the content (questions, options, topics, wording, pronunciation,
images). Things outside the content (speech recognition, the app, the AI trainer's behaviour) go to `ours` on
the run, emailed to the NudgeLab team, never to trainers.

Each run replaces the training's earlier *open* recommendations (status "replaced"); ones a trainer marked
done or dismissed stay, and are shown to Claude so it doesn't suggest them again.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntId, UtcDateTime

OPEN, DONE, DISMISSED, REPLACED = "open", "done", "dismissed", "replaced"


class DashRecommendationRun(Base):
    """One nightly run for one training: what was looked at, and the items for the NudgeLab team."""

    __tablename__ = "dash_recommendation_runs"
    __table_args__ = (Index("ix_dash_recommendation_runs_training", "training_id", "run_date"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    training_id: Mapped[str] = mapped_column(String(50), nullable=False)
    version_id: Mapped[int | None] = mapped_column(Integer)
    window_from: Mapped[date] = mapped_column(Date, nullable=False)
    window_to: Mapped[date] = mapped_column(Date, nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    ours: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )


class DashRecommendation(Base):
    __tablename__ = "dash_recommendations"
    __table_args__ = (Index("ix_dash_recommendations_training", "training_id", "status"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("dash_recommendation_runs.id"), nullable=False)
    training_id: Mapped[str] = mapped_column(String(50), nullable=False)
    version_id: Mapped[int | None] = mapped_column(Integer)
    area: Mapped[str] = mapped_column(String(20), nullable=False)  # question, topic, opening, length, ...
    target: Mapped[str | None] = mapped_column(String(80))  # "Question 3", "Topic 2"
    impact: Mapped[str] = mapped_column(String(10), nullable=False)  # high, medium, low
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    examples: Mapped[list[str] | None] = mapped_column(JSON)  # session ids
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=OPEN)
    status_note: Mapped[str | None] = mapped_column(String(300))
    status_by: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("dash_users.id"))
    status_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.utc_timestamp(6), nullable=False
    )
