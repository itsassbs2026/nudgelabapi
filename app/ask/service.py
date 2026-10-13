"""Ask NudgeLab's conversations, like / dislike, usage and the Admin log (docs/ASK_NUDGELAB.md)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ask.engine import asked_today, day_start_utc
from app.models.ask import AskConversation, AskFeedback, AskMessage
from app.models.dashboard import DashUser
from app.services import settings_table
from app.utils.errors import ApiError


def _owned(db: Session, user_id: int, conversation_id: int) -> AskConversation:
    convo = db.get(AskConversation, conversation_id)
    if convo is None or convo.user_id != user_id or convo.hidden:
        raise ApiError(404, "not_found", "That conversation isn't there.")
    return convo


def conversations(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AskConversation)
        .where(AskConversation.user_id == user_id, AskConversation.hidden.is_(False))
        .order_by(AskConversation.updated_at.desc())
        .limit(200)
    ).scalars()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in rows]


def messages(db: Session, user_id: int, conversation_id: int) -> list[dict[str, Any]]:
    _owned(db, user_id, conversation_id)
    rows = db.execute(
        select(AskMessage, AskFeedback.rating, AskFeedback.comment)
        .outerjoin(AskFeedback, (AskFeedback.message_id == AskMessage.id) & (AskFeedback.user_id == user_id))
        .where(AskMessage.conversation_id == conversation_id)
        .order_by(AskMessage.id)
    ).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "error": bool(m.error),
            "created_at": m.created_at,
            "rating": rating,
            "comment": comment,
        }
        for m, rating, comment in rows
    ]


def rename(db: Session, user_id: int, conversation_id: int, title: str) -> dict[str, Any]:
    convo = _owned(db, user_id, conversation_id)
    title = " ".join(title.split())[:120]
    if not title:
        raise ApiError(422, "empty", "Give the conversation a name.")
    convo.title = title
    db.commit()
    return {"id": convo.id, "title": convo.title, "updated_at": convo.updated_at}


def hide(db: Session, user_id: int, conversation_id: int) -> None:
    """'Delete' for its owner: hidden from their history, kept in the log."""
    _owned(db, user_id, conversation_id).hidden = True
    db.commit()


def rate(db: Session, user_id: int, message_id: int, rating: int, comment: str | None) -> dict[str, Any]:
    msg = db.get(AskMessage, message_id)
    if msg is None or msg.role != "assistant" or msg.user_id != user_id:
        raise ApiError(404, "not_found", "That answer isn't there.")
    row = db.get(AskFeedback, (message_id, user_id))
    if row is None:
        row = AskFeedback(message_id=message_id, user_id=user_id, rating=rating)
        db.add(row)
    row.rating = rating
    row.comment = (" ".join(comment.split())[:500] or None) if comment else None
    db.commit()
    return {"message_id": message_id, "rating": row.rating, "comment": row.comment}


def usage(db: Session, user: DashUser) -> dict[str, Any]:
    limit = int(settings_table.get(db, "ask.daily_limit", 50))
    return {
        "enabled": bool(settings_table.get(db, "ask.enabled", True)),
        "asked_today": asked_today(db, user.id, user.timezone or "America/Chicago"),
        "daily_limit": limit,
    }


def log(
    db: Session,
    *,
    tz: str,
    date_from: date | None,
    date_to: date | None,
    user_id: int | None,
    disliked: bool,
    search: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Admin > Ask NudgeLab log: each answer with its question, who asked, the tools, cost, time and
    feedback."""
    q = AskMessage.__table__.alias("q")
    a = AskMessage
    question = (
        select(q.c.content)
        .where(q.c.conversation_id == a.conversation_id, q.c.role == "user", q.c.id < a.id)
        .order_by(q.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    conditions = [a.role == "assistant"]
    if date_from:
        conditions.append(a.created_at >= day_start_utc(tz, _as_datetime(date_from, tz)))
    if date_to:
        conditions.append(a.created_at < day_start_utc(tz, _as_datetime(date_to + timedelta(days=1), tz)))
    if user_id:
        conditions.append(a.user_id == user_id)
    if disliked:
        conditions.append(AskFeedback.rating == -1)
    if search and search.strip():
        term = f"%{search.strip()}%"
        conditions.append(or_(a.content.like(term), question.like(term)))
    base = (
        select(a, question.label("question"), DashUser.full_name, AskFeedback.rating, AskFeedback.comment)
        .join(DashUser, DashUser.id == a.user_id)
        .outerjoin(AskFeedback, (AskFeedback.message_id == a.id) & (AskFeedback.user_id == a.user_id))
        .where(*conditions)
    )
    total = int(db.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    cost = db.execute(select(func.coalesce(func.sum(base.subquery().c.cost), 0))).scalar_one()
    rows = db.execute(base.order_by(a.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "total": total,
        "cost": float(cost or 0),
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "message_id": m.id,
                "conversation_id": m.conversation_id,
                "asked_by": name,
                "question": qtext,
                "answer": m.content,
                "tools": [c.get("name") for c in (m.tool_calls or [])],
                "model": m.model,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "cost": float(m.cost) if m.cost is not None else None,
                "seconds": round((m.duration_ms or 0) / 1000, 1),
                "error": m.error,
                "rating": rating,
                "comment": comment,
                "created_at": m.created_at,
            }
            for m, qtext, name, rating, comment in rows
        ],
    }


def _as_datetime(day: date, tz: str) -> Any:
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(12), tzinfo=ZoneInfo(tz))
