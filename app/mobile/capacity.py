"""Are the Nudge trainers all busy? Checked before the app gets a session (docs/APP_HANDOFF.md §3.3).

Capacity is the sum over agent servers that checked in recently and are taking calls (`agent_servers`, written
by each server every 30 seconds). Live calls are the training and preview rooms in LiveKit. When live calls
reach the capacity the app is told to come back later, instead of joining a call no trainer can take.

It never blocks by mistake: with no server rows at all (the check isn't set up), or LiveKit unreadable, the
session goes ahead as before. Bot-test rooms count too: they use a trainer like anyone else.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.agent_servers import AgentServer
from app.services import live
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

CALL_ROOM_PREFIXES = ("nl-", "pv-")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def capacity(db: Session, settings: Settings) -> int | None:
    """Calls the running servers can take, or None when no server has ever checked in."""
    if db.execute(select(func.count()).select_from(AgentServer)).scalar_one() == 0:
        return None
    fresh = _now() - timedelta(seconds=settings.agent_server_stale_seconds)
    total = db.execute(
        select(func.coalesce(func.sum(AgentServer.capacity), 0)).where(
            AgentServer.last_seen >= fresh, AgentServer.accepting.is_(True)
        )
    ).scalar_one()
    return int(total)


def live_calls(settings: Settings) -> int | None:
    """Training and preview calls in LiveKit now, or None when LiveKit can't be read."""
    try:
        rooms = asyncio.run(live.fetch_rooms(settings))
    except ApiError:
        return None
    in_use = [
        r for r in rooms if (r.name or "").startswith(CALL_ROOM_PREFIXES) and int(r.num_participants or 0)
    ]
    return len(in_use)


def check(db: Session, settings: Settings) -> None:
    """Raise 503 trainers_busy when every trainer is taken."""
    if not settings.app_busy_check:
        return
    room_for = capacity(db, settings)
    if room_for is None:
        return
    calls = live_calls(settings) if room_for > 0 else 0
    if calls is None:
        logger.warning("busy_check_skipped", reason="livekit_unreadable", capacity=room_for)
        return
    if room_for > 0 and calls < room_for:
        return
    logger.warning("trainers_busy", capacity=room_for, live_calls=calls)
    raise ApiError(
        503,
        "trainers_busy",
        settings.app_busy_message,
        {"retry_after_minutes": settings.app_busy_retry_minutes},
    )
