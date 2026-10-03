"""Live sessions right now (SPEC §7.2 Overview, §8.2 `GET /live`): LiveKit's room list, read-only.

The agent's rooms are named `nl-<training_id>-<uid>-<tag>` (the tester page and the app). Bot tests use a tag
starting with `bot`. Only names, trainings and start times are returned: nothing about what is being said.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.reference.agent_tables import trainings, vw_trainees
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

ROOM_NAME = re.compile(r"^nl-(?P<training>[\w-]+?)-(?P<uid>\d+)-(?P<tag>[0-9a-z]+)$")
TIMEOUT_SECONDS = 5


async def _list_rooms(settings: Settings) -> list[Any]:
    async with api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    ) as lk:
        response = await lk.room.list_rooms(api.ListRoomsRequest())
    return list(response.rooms)


async def fetch_rooms(settings: Settings) -> list[Any]:
    try:
        return await asyncio.wait_for(_list_rooms(settings), TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("live_rooms_failed", error=type(exc).__name__)
        raise ApiError(503, "live_unavailable", "Live sessions can't be read right now.") from exc


def describe(db: Session, rooms: list[Any], *, include_tests: bool) -> list[dict[str, Any]]:
    """Training sessions among the rooms, with trainee names and training titles; newest first."""
    parsed = []
    for room in rooms:
        match = ROOM_NAME.match(room.name or "")
        if not match:
            continue
        is_test = match["tag"].startswith("bot")
        if is_test and not include_tests:
            continue
        created = int(room.creation_time or 0)
        parsed.append(
            {
                "room": room.name,
                "training_id": match["training"],
                "uid": int(match["uid"]),
                "participants": int(room.num_participants or 0),
                "started_at": datetime.fromtimestamp(created, UTC).replace(tzinfo=None) if created else None,
                "is_test": is_test,
            }
        )
    uids = {r["uid"] for r in parsed}
    names: dict[int, str | None] = {}
    if uids:
        names = {
            int(uid): name
            for uid, name in db.execute(
                select(vw_trainees.c.uid, vw_trainees.c.name).where(vw_trainees.c.uid.in_(uids))
            )
        }
    titles = {tid: title for tid, title in db.execute(select(trainings.c.training_id, trainings.c.title))}
    for r in parsed:
        r["name"] = names.get(r["uid"])
        r["training_title"] = titles.get(r["training_id"])
    parsed.sort(key=lambda r: r["started_at"] or datetime.min, reverse=True)
    return parsed
