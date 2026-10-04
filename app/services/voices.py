"""Voices and voice samples (SPEC 10.3): the voices Anne can use, and any text read aloud in one of them.

Samples are made on demand by Amazon Polly with the same engine the agent uses for that voice (the voice's
`engine`, generative by default), through the server's instance role (`polly:SynthesizeSpeech`,
deploy/iam-policy-stage2.json). Nothing is stored. A sample costs a fraction of a cent; requests are limited
per dashboard user and in length.
"""

from __future__ import annotations

from typing import Any

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.reference.agent_tables import training_voices
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)


def list_voices(db: Session) -> list[dict[str, Any]]:
    v = training_voices.c
    rows = db.execute(select(training_voices).order_by(v.sort_order, v.voice_id)).all()
    return [
        {
            "voice_id": r.voice_id,
            "display_name": r.display_name,
            "language_code": r.language_code,
            "gender": r.gender,
            "engine": r.engine or "generative",
            "is_default": bool(r.is_default),
            "is_active": bool(r.is_active),
            "notes": r.notes,
        }
        for r in rows
    ]


def find_voice(db: Session, voice_id: str) -> Any:
    row = db.execute(select(training_voices).where(training_voices.c.voice_id == voice_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "That voice isn't in the list.")
    return row


def synthesize(settings: Settings, voice_id: str, engine: str, text: str) -> bytes:
    """MP3 audio of `text` read by `voice_id`."""
    try:
        polly = boto3.client("polly", region_name=settings.polly_region)
        response = polly.synthesize_speech(
            Text=text, VoiceId=voice_id, Engine=engine, OutputFormat="mp3", SampleRate="24000"
        )
        return bytes(response["AudioStream"].read())
    except (BotoCoreError, ClientError) as exc:
        logger.warning("voice_sample_failed", voice=voice_id, error=type(exc).__name__)
        raise ApiError(503, "samples_unavailable", "Voice samples aren't available right now.") from exc
