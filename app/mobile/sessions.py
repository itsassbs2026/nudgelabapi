"""Starting a voice session from the app (docs/APP_HANDOFF.md §3.3).

The LiveKit token is signed with the LiveKit secret and carries the agent dispatch and its metadata (uid,
training, start over, client). The app can't change any of it, so the agent trusts the uid as it does for the
tester page. The room name follows the agent's pattern, `nl-<training>-<uid>-<random>`, which the dashboard's
Live page and the reports already understand.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mobile.passes import Employee
from app.mobile.persona import Chosen
from app.models.app import AppSessionStart
from app.reference.agent_tables import trainings
from app.services import live
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

CLIENT_LABEL = "flutter"
# Sessions started from the employee portal's training page (2026-10-29): shown apart in the reports.
CLIENT_LABELS = {"wanaka": CLIENT_LABEL, "portal": "portal"}

# 2026-11-05: an app tapped Start 7 times in 28 seconds and got 7 rooms, each with a trainer talking to
# nobody. A start this soon after the last one, for the same training, gets the same room back. The agent
# waits 60 s for someone to join (RECONNECT_GRACE_SECONDS), so 40 s leaves room to spare.
REUSE_SECONDS = 40


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def recent_room(db: Session, settings: Settings, uid: int, training_id: str, *, start_over: bool,
                chosen: Chosen) -> str | None:  # fmt: skip
    """The room of this person's start for this training in the last REUSE_SECONDS, if it can still take them.

    Reused only with the same settings (the trainer's metadata is fixed when the room is made) and only
    while the trainer is in the room, waiting. A room that's gone, an open room without a trainer (the
    session ended, e.g. "trainee left"), or LiveKit unreadable: a new room, as before. (A gone room isn't
    made again under the same name: its session row and recording would be shared with the new call.)
    """
    if not settings.livekit_configured:
        return None
    last = db.execute(
        select(AppSessionStart)
        .where(
            AppSessionStart.uid == uid,
            AppSessionStart.training_id == training_id,
            AppSessionStart.created_at >= _now() - timedelta(seconds=REUSE_SECONDS),
        )
        .order_by(AppSessionStart.created_at.desc(), AppSessionStart.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None or (bool(last.start_over), last.trainer_name, last.voice_id) != (
        start_over,
        chosen.spoken,
        chosen.voice,
    ):
        return None
    state = live.room_state(settings, last.room_name)
    if state == "agent":
        logger.info("session_start_reused", uid=uid, room=last.room_name, room_state=state)
        return last.room_name
    return None


def start(
    db: Session,
    settings: Settings,
    employee: Employee,
    training_id: str,
    chosen: Chosen,
    *,
    start_over: bool,
    ip: str | None,
    room_name: str | None = None,
) -> dict[str, Any]:
    if not settings.livekit_configured:
        raise ApiError(503, "sessions_unavailable", "Voice sessions aren't available right now.")
    uid = employee.uid
    room_name = room_name or f"nl-{training_id}-{uid}-{secrets.token_hex(3)}"
    metadata: dict[str, Any] = {
        "uid": uid,
        "training_id": training_id,
        "reset": start_over,
        "client": CLIENT_LABELS.get(employee.issuer, CLIENT_LABEL),
    }
    # The trainer's name and voice (app/mobile/persona.py). Without a name the agent uses the training's own.
    if chosen.spoken:
        metadata["trainer_name"] = chosen.spoken
    if chosen.voice:
        metadata["voice"] = chosen.voice
    ttl = timedelta(minutes=settings.app_session_token_minutes)
    completion_type = db.execute(
        select(trainings.c.completion_type).where(trainings.c.training_id == training_id)
    ).scalar_one_or_none()
    agent_name = settings.agent_name_for(completion_type)
    dispatch = api.RoomAgentDispatch(agent_name=agent_name, metadata=json.dumps(metadata))
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        # One identity per room: a start that reuses the room replaces the earlier connection (LiveKit drops
        # it), and the agent takes it as the same trainee coming back, not a stranger it ignores.
        .with_identity(f"app-{uid}-{room_name.rsplit('-', 1)[-1]}")
        .with_name(str(employee.profile.get("name") or uid))
        .with_ttl(ttl)
        .with_grants(api.VideoGrants(room_join=True, room=room_name, can_update_own_metadata=True))
        .with_room_config(api.RoomConfiguration(agents=[dispatch]))
        .to_jwt()
    )
    db.add(
        AppSessionStart(
            uid=uid,
            training_id=training_id,
            room_name=room_name,
            start_over=start_over,
            pass_jti=employee.jti,
            ip=ip,
            trainer_name=chosen.spoken,
            voice_id=chosen.voice,
        )
    )
    db.commit()
    return {
        "server_url": settings.livekit_url,
        "participant_token": token,
        "room_name": room_name,
        "expires_in": int(ttl.total_seconds()),
        "trainer_name": chosen.spoken,
        "trainer_voice": chosen.voice,
    }
