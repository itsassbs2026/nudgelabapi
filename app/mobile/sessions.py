"""Starting a voice session from the app (docs/APP_HANDOFF.md §3.3).

The LiveKit token is signed with the LiveKit secret and carries the agent dispatch and its metadata (uid,
training, start over, client). The app can't change any of it, so the agent trusts the uid as it does for the
tester page. The room name follows the agent's pattern, `nl-<training>-<uid>-<random>`, which the dashboard's
Live page and the reports already understand.
"""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mobile.passes import Employee
from app.mobile.persona import Chosen
from app.models.app import AppSessionStart
from app.reference.agent_tables import trainings
from app.utils.errors import ApiError

CLIENT_LABEL = "flutter"


def start(
    db: Session,
    settings: Settings,
    employee: Employee,
    training_id: str,
    chosen: Chosen,
    *,
    start_over: bool,
    ip: str | None,
) -> dict[str, Any]:
    if not settings.livekit_configured:
        raise ApiError(503, "sessions_unavailable", "Voice sessions aren't available right now.")
    uid = employee.uid
    room_name = f"nl-{training_id}-{uid}-{secrets.token_hex(3)}"
    metadata: dict[str, Any] = {
        "uid": uid,
        "training_id": training_id,
        "reset": start_over,
        "client": CLIENT_LABEL,
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
        .with_identity(f"app-{uid}-{secrets.token_hex(2)}")
        .with_name(str(employee.profile.get("name") or uid))
        .with_ttl(ttl)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
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
