"""Ask NudgeLab (docs/ASK_NUDGELAB.md): the chat for everyone with a dashboard login, and its Admin pages
(Settings, and the log of every question and answer)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ask import engine, service
from app.auth.deps import CurrentUser, get_user, require_admin
from app.config import Settings, get_settings
from app.db import SessionLocal, get_db
from app.routers.auth import client_ip
from app.services import settings_table

router = APIRouter(tags=["ask"])


def session_factory() -> Callable[[], AbstractContextManager[Session]]:
    """The answer streams after the request's own session is closed, so it opens one (tests swap this)."""
    return lambda: closing(SessionLocal())


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = Field(default=None, ge=1)


class ConversationOut(BaseModel):
    id: int
    title: str
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    error: bool
    created_at: datetime
    rating: int | None
    comment: str | None


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FeedbackIn(BaseModel):
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=500)


class FeedbackOut(BaseModel):
    message_id: int
    rating: int
    comment: str | None


class UsageOut(BaseModel):
    enabled: bool
    asked_today: int
    daily_limit: int


@router.post("/ask/chat")
def chat(
    body: ChatIn,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sessions: Callable[[], AbstractContextManager[Session]] = Depends(session_factory),
) -> StreamingResponse:
    """The answer to a question, streamed as server-sent events (the event types: app/ask/engine.py)."""
    convo, _ = engine.start(db, current.user, body.conversation_id, body.question)
    stream = engine.stream_answer(
        sessions,
        settings,
        current.user.id,
        current.user.full_name,
        current.user.timezone or "America/Chicago",
        convo.id,
    )
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Conversation-Id": str(convo.id)}
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


@router.get("/ask/usage", response_model=UsageOut)
def usage(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    return service.usage(db, current.user)


@router.get("/ask/conversations", response_model=list[ConversationOut])
def conversations(current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    return service.conversations(db, current.user.id)


@router.get("/ask/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def messages(
    conversation_id: int = Path(ge=1), current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Any:
    return service.messages(db, current.user.id, conversation_id)


@router.patch("/ask/conversations/{conversation_id}", response_model=ConversationOut)
def rename(
    body: RenameIn,
    conversation_id: int = Path(ge=1),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.rename(db, current.user.id, conversation_id, body.title)


@router.delete("/ask/conversations/{conversation_id}", status_code=204)
def delete(
    conversation_id: int = Path(ge=1), current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)
) -> Response:
    service.hide(db, current.user.id, conversation_id)
    return Response(status_code=204)


@router.put("/ask/messages/{message_id}/feedback", response_model=FeedbackOut)
def feedback(
    body: FeedbackIn,
    message_id: int = Path(ge=1),
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
) -> Any:
    return service.rate(db, current.user.id, message_id, body.rating, body.comment)


# -- Admin --------------------------------------------------------------------------------------------------


class SettingOut(BaseModel):
    key: str
    value: str
    type: str
    description: str
    updated_at: datetime


class SettingIn(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class LogItem(BaseModel):
    message_id: int
    conversation_id: int
    asked_by: str | None
    question: str | None
    answer: str
    tools: list[str | None]
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    seconds: float
    error: str | None
    rating: int | None
    comment: str | None
    created_at: datetime


class LogPage(BaseModel):
    total: int
    cost: float
    page: int
    page_size: int
    items: list[LogItem]


@router.get("/admin/settings", response_model=list[SettingOut])
def list_settings(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> Any:
    return settings_table.list_settings(db)


@router.put("/admin/settings/{key}", response_model=SettingOut)
def set_setting(
    body: SettingIn,
    request: Request,
    key: str = Path(min_length=1, max_length=64, pattern=r"^[a-z0-9_.]+$"),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return settings_table.set_value(db, current, key, body.value, client_ip(request))


@router.get("/admin/ask/log", response_model=LogPage)
def ask_log(
    date_from: date | None = None,
    date_to: date | None = None,
    user_id: int | None = Query(default=None, ge=1),
    disliked: bool = False,
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Any:
    return service.log(
        db,
        tz=current.user.timezone or "America/Chicago",
        date_from=date_from,
        date_to=date_to,
        user_id=user_id,
        disliked=disliked,
        search=search,
        page=page,
        page_size=page_size,
    )
