"""Preview calls (SPEC 10.5, Phase 15): a trainer talks to Anne running one version of a training, drafts too.

The LiveKit token dispatches the agent with this metadata:

    uid            the trainer's reserved preview uid, 900000 + their dashboard user id (never an employee)
    training_id    the version's training
    client         "preview" (reports leave it out)
    preview        the signed pass: {version_id, training_id, uid, start, exp, sig}
    first_name     the trainer's first name, so Anne greets them
    voice, profile, trainer_name, location_type   only when the trainer picked them

`sig` is HMAC-SHA256 with PREVIEW_SECRET over `v1|<version_id>|<training_id>|<uid>|<start>|<exp>`, exactly as
the agent's preview.py checks it. Without a valid pass the agent refuses the room, so a token minted elsewhere
can't make a session skip progress, and a preview can't turn into a normal session. The agent writes the
session, its transcript and usage, and nothing else: no progress, completions, acknowledgments, feedback or
recording.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from typing import Any

from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.mobile.persona import Voices, spoken_name
from app.reference.agent_tables import training_profiles
from app.services import audit, live
from app.services.audit import AuditAction
from app.studio import service
from app.utils.errors import ApiError

PREVIEW_UID_BASE = 900000
STARTS = ("beginning", "after_topics")
LOCATION_TYPES = ("fiber", "aia_only", "alaska_only")  # the agent's LOCATION_LABELS
CLIENT_LABEL = "preview"


def preview_uid(user_id: int) -> int:
    return PREVIEW_UID_BASE + user_id


def sign(secret: str, version_id: int, training_id: str, uid: int, start: str, exp: int) -> str:
    message = f"v1|{version_id}|{training_id}|{uid}|{start}|{exp}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _live_preview(settings: Settings, uid: int) -> str | None:
    """A preview room of this trainer's with a person still in it, if any (one at a time). Only people count:
    after a call ends the agent stays a minute or two, and LiveKit keeps the empty room a few more."""

    async def find() -> str | None:
        rooms = await live.fetch_rooms(settings)
        marker = f"-{uid}-"
        for r in rooms:
            name = r.name or ""
            mine = name.startswith("pv-") and marker in name and r.num_participants
            if mine and await live.people_in(settings, name):
                return name
        return None

    try:
        return asyncio.run(find())
    except ApiError as exc:
        raise ApiError(503, "previews_unavailable", "Preview calls aren't available right now.") from exc


def _profile(db: Session, profile_id: str | None) -> str | None:
    if profile_id is None:
        return None
    row = db.execute(
        select(training_profiles.c.profile_id).where(
            training_profiles.c.profile_id == profile_id, training_profiles.c.is_active == 1
        )
    ).first()
    if row is None:
        raise ApiError(422, "unknown_setup", "Choose a setup that's switched on.")
    return str(row.profile_id)


def start(
    db: Session,
    settings: Settings,
    current: CurrentUser,
    version_id: int,
    choices: dict[str, Any],
    ip: str | None,
) -> dict[str, Any]:
    if not settings.previews_configured:
        raise ApiError(503, "previews_unavailable", "Preview calls aren't available right now.")
    row = service._version(db, version_id)
    if row.content is None:
        raise ApiError(422, "no_content", "This version has no content yet.")
    errors = service.validate_version(db, version_id)["errors"]
    if errors:
        raise ApiError(422, "has_errors", "Fix the errors this version's checks found before calling it.",
                       {"errors": len(errors)})  # fmt: skip

    voice = None
    if choices.get("voice"):
        voice = Voices(db).canonical(choices["voice"])
        if voice is None:
            raise ApiError(422, "unknown_voice", "Choose a voice that's switched on.")
    profile = _profile(db, choices.get("profile"))
    trainer_name = None
    if choices.get("trainer_name"):
        trainer_name = spoken_name(choices["trainer_name"])
        if trainer_name is None:
            raise ApiError(422, "bad_trainer_name", "A trainer name is one plain first name.")

    uid = preview_uid(current.user.id)
    running = _live_preview(settings, uid)
    if running:
        raise ApiError(409, "preview_running", "You already have a preview call open. Hang it up first.",
                       {"room": running})  # fmt: skip

    training_id = str(row.training_id)
    start_at = choices.get("start") or "beginning"
    minutes = settings.preview_minutes
    exp = int(time.time()) + minutes * 60
    assert settings.preview_secret  # previews_configured
    metadata: dict[str, Any] = {
        "uid": uid,
        "training_id": training_id,
        "client": CLIENT_LABEL,
        "preview": {
            "version_id": version_id,
            "training_id": training_id,
            "uid": uid,
            "start": start_at,
            "exp": exp,
            "sig": sign(settings.preview_secret, version_id, training_id, uid, start_at, exp),
        },
        "first_name": spoken_name(current.user.full_name) or "",
    }
    for key, value in (("voice", voice), ("profile", profile), ("trainer_name", trainer_name),
                       ("location_type", choices.get("location_type"))):  # fmt: skip
        if value:
            metadata[key] = value

    room_name = f"pv-{training_id}-{uid}-{secrets.token_hex(3)}"
    dispatch = api.RoomAgentDispatch(agent_name=settings.livekit_agent_name, metadata=json.dumps(metadata))
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"preview-{current.user.id}-{secrets.token_hex(2)}")
        .with_name(current.user.full_name)
        .with_ttl(timedelta(minutes=minutes))
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_room_config(api.RoomConfiguration(agents=[dispatch]))
        .to_jwt()
    )
    audit.record(db, AuditAction.PREVIEW_CALL, actor_user_id=current.user.id, target_type="version",
                 target_id=version_id, details={"room": room_name, "start": start_at}, ip=ip)  # fmt: skip
    db.commit()
    return {
        "server_url": settings.livekit_url,
        "participant_token": token,
        "room_name": room_name,
        "expires_in": minutes * 60,
        "version_id": version_id,
        "training_id": training_id,
    }
