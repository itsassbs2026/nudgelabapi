"""Voices, setups and testers (SPEC 10.3, Phase 14). Admin only.

The agent reads voices and setups at the start of every session, so a change here applies to the next session.
That's why the rules are strict:
- one default voice and one default setup, always switched on (the tables enforce it too: a unique default
  marker and a check constraint), changed in one statement, as the agent's README does;
- a setup's model comes from SETUP_MODELS (models the agent is known to run), and effort only where the model
  takes it: an unknown model id or an effort on Haiku would fail every session on that setup;
- testers' access codes are made and hashed exactly as the agent's web.py does (`_hash`), so existing codes
  keep working after the import from testers.json.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from decimal import Decimal
from typing import Any

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.dashboard import DashUser
from app.models.testers import Tester
from app.reference.agent_tables import training_profiles, training_voices, trainings
from app.services import audit
from app.services.audit import AuditAction
from app.services.voices import list_voices
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

# Models a setup can use: the Bedrock inference profiles the agent runs, and whether they take `effort`.
SETUP_MODELS: dict[str, dict[str, Any]] = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"label": "Claude Haiku 4.5", "effort": False},
    "us.anthropic.claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "effort": True},
}
# The columns the API may UPDATE: also the column-level grants in deploy/db-grants-0009-admin.sql (tested).
VOICE_UPDATABLE = frozenset({"is_active", "is_default", "notes", "sort_order"})
PROFILE_UPDATABLE = frozenset(
    {"display_name", "description", "llm_model", "llm_effort", "llm_max_output_tokens", "tts_engine",
     "voice_id", "llm_input_per_m", "llm_cached_per_m", "llm_cache_write_per_m", "llm_output_per_m",
     "tts_per_m_chars", "stt_per_minute", "is_default", "is_active", "allow_request", "notes"}
)  # fmt: skip
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # web.py's: no 0/O or 1/I/L
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


# --- Voices ----------------------------------------------------------------------------------------------


def _voice(db: Session, voice_id: str) -> Any:
    row = db.execute(select(training_voices).where(training_voices.c.voice_id == voice_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "That voice isn't in the list.")
    return row


def update_voice(
    db: Session, current: CurrentUser, voice_id: str, changes: dict[str, Any], ip: str | None
) -> list[dict[str, Any]]:
    voice = _voice(db, voice_id)
    if changes.get("is_active") is False and voice.is_default:
        raise ApiError(
            409, "default_voice", "The default voice can't be switched off. Make another voice the default."
        )
    if changes:
        db.execute(update(training_voices).where(training_voices.c.voice_id == voice_id).values(**changes))
    audit.record(db, AuditAction.SETTINGS_CHANGED, actor_user_id=current.user.id, target_type="voice",
                 target_id=voice_id, details={"fields": sorted(changes)}, ip=ip)  # fmt: skip
    db.commit()
    return list_voices(db)


def make_default_voice(
    db: Session, current: CurrentUser, voice_id: str, ip: str | None
) -> list[dict[str, Any]]:
    voice = _voice(db, voice_id)
    if not voice.is_active:
        raise ApiError(409, "voice_off", "Switch the voice on before making it the default.")
    # Clear, then set, in one transaction: MySQL checks the unique default marker row by row, so one UPDATE
    # flipping both rows can collide. Readers never see the in-between state (it isn't committed).
    db.execute(update(training_voices).where(training_voices.c.is_default.is_(True)).values(is_default=False))
    db.execute(update(training_voices).where(training_voices.c.voice_id == voice_id).values(is_default=True))
    audit.record(db, AuditAction.SETTINGS_CHANGED, actor_user_id=current.user.id, target_type="voice",
                 target_id=voice_id, details={"default": True}, ip=ip)  # fmt: skip
    db.commit()
    return list_voices(db)


def polly_voices(settings: Settings) -> list[dict[str, Any]]:
    """Polly's generative English voices."""
    try:
        polly = boto3.client("polly", region_name=settings.polly_region)
        found: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            page = polly.describe_voices(Engine="generative", **({"NextToken": token} if token else {}))
            found.extend(page.get("Voices", []))
            token = page.get("NextToken")
            if not token:
                break
    except (BotoCoreError, ClientError) as exc:
        logger.warning("polly_voices_failed", error=type(exc).__name__)
        raise ApiError(503, "voices_unavailable", "Polly's voice list isn't available right now.") from exc
    return [
        {
            "voice_id": v["Id"],
            "name": v.get("Name", v["Id"]),
            "gender": v.get("Gender", ""),
            "language_code": v.get("LanguageCode", ""),
            "language_name": v.get("LanguageName", ""),
        }
        for v in found
        if str(v.get("LanguageCode", "")).startswith("en-")
    ]


def available_voices(db: Session, settings: Settings) -> list[dict[str, Any]]:
    listed = {v["voice_id"] for v in list_voices(db)}
    return sorted((v for v in polly_voices(settings) if v["voice_id"] not in listed), key=lambda v: v["name"])


def add_voice(
    db: Session, settings: Settings, current: CurrentUser, voice_id: str, ip: str | None
) -> list[dict[str, Any]]:
    if db.execute(select(training_voices.c.voice_id).where(training_voices.c.voice_id == voice_id)).first():
        raise ApiError(409, "voice_exists", "That voice is already in the list.")
    voice = next((v for v in polly_voices(settings) if v["voice_id"] == voice_id), None)
    if voice is None:
        raise ApiError(422, "unknown_voice", "Polly has no generative English voice with that name.")
    last = db.execute(select(func.max(training_voices.c.sort_order))).scalar() or 0
    db.execute(
        insert(training_voices).values(
            voice_id=voice["voice_id"],
            display_name=voice["name"],
            language_code=voice["language_code"],
            gender=voice["gender"] if voice["gender"] in ("Female", "Male") else "Female",
            engine="generative",
            is_default=False,
            is_active=True,
            sort_order=int(last) + 10,
        )
    )
    audit.record(db, AuditAction.SETTINGS_CHANGED, actor_user_id=current.user.id, target_type="voice",
                 target_id=voice_id, details={"added": True}, ip=ip)  # fmt: skip
    db.commit()
    return list_voices(db)


# --- Setups (training_profiles) --------------------------------------------------------------------------

PRICE_FIELDS = (
    "llm_input_per_m",
    "llm_cached_per_m",
    "llm_cache_write_per_m",
    "llm_output_per_m",
    "tts_per_m_chars",
    "stt_per_minute",
)


def _money(value: Any) -> float | None:
    return float(value) if isinstance(value, Decimal | int | float) else None


def list_profiles(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(training_profiles).order_by(training_profiles.c.profile_id)).all()
    out = []
    for r in rows:
        model = SETUP_MODELS.get(r.llm_model)
        out.append(
            {
                "profile_id": r.profile_id,
                "display_name": r.display_name,
                "description": r.description,
                "llm_model": r.llm_model,
                "llm_model_label": model["label"] if model else r.llm_model,
                "llm_model_known": model is not None,
                "llm_effort": r.llm_effort,
                "llm_max_output_tokens": r.llm_max_output_tokens,
                "tts_engine": r.tts_engine,
                "voice_id": r.voice_id,
                **{field: _money(getattr(r, field)) for field in PRICE_FIELDS},
                "is_default": bool(r.is_default),
                "is_active": bool(r.is_active),
                "allow_request": bool(r.allow_request),
                "notes": r.notes,
            }
        )
    return out


def models() -> list[dict[str, Any]]:
    return [{"llm_model": key, "label": m["label"], "effort": m["effort"]} for key, m in SETUP_MODELS.items()]


def update_profile(
    db: Session, current: CurrentUser, profile_id: str, changes: dict[str, Any], ip: str | None
) -> list[dict[str, Any]]:
    row = db.execute(select(training_profiles).where(training_profiles.c.profile_id == profile_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "That setup doesn't exist.")
    model = changes.get("llm_model", row.llm_model)
    if "llm_model" in changes and model not in SETUP_MODELS:
        raise ApiError(422, "unknown_model", "Choose one of the listed models.")
    effort = changes.get("llm_effort", row.llm_effort)
    touched = "llm_effort" in changes or "llm_model" in changes
    if touched and effort and model in SETUP_MODELS and not SETUP_MODELS[model]["effort"]:
        raise ApiError(422, "no_effort", f"{SETUP_MODELS[model]['label']} doesn't take an effort setting.")
    if changes.get("voice_id"):
        voice = db.execute(
            select(training_voices).where(training_voices.c.voice_id == changes["voice_id"])
        ).first()
        if voice is None or not voice.is_active:
            raise ApiError(422, "voice_off", "Choose a voice that's switched on.")
    if changes.get("is_active") is False and row.is_default:
        raise ApiError(
            409, "default_setup", "The default setup can't be switched off. Make another setup the default."
        )
    if changes:
        db.execute(
            update(training_profiles).where(training_profiles.c.profile_id == profile_id).values(**changes)
        )
    audit.record(db, AuditAction.SETTINGS_CHANGED, actor_user_id=current.user.id, target_type="setup",
                 target_id=profile_id, details={"fields": sorted(changes)}, ip=ip)  # fmt: skip
    db.commit()
    return list_profiles(db)


def make_default_profile(
    db: Session, current: CurrentUser, profile_id: str, ip: str | None
) -> list[dict[str, Any]]:
    row = db.execute(select(training_profiles).where(training_profiles.c.profile_id == profile_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "That setup doesn't exist.")
    if not row.is_active:
        raise ApiError(409, "setup_off", "Switch the setup on before making it the default.")
    # As make_default_voice: clear, then set, in one transaction.
    db.execute(
        update(training_profiles).where(training_profiles.c.is_default.is_(True)).values(is_default=False)
    )
    db.execute(
        update(training_profiles).where(training_profiles.c.profile_id == profile_id).values(is_default=True)
    )
    audit.record(db, AuditAction.SETTINGS_CHANGED, actor_user_id=current.user.id, target_type="setup",
                 target_id=profile_id, details={"default": True}, ip=ip)  # fmt: skip
    db.commit()
    return list_profiles(db)


# --- Testers ---------------------------------------------------------------------------------------------


def code_hash(code: str) -> str:
    """As the agent's web.py `_hash`: trimmed, uppercased, SHA-256."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def new_code() -> str:
    return "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def _runnable(db: Session, training_ids: list[str]) -> list[str]:
    """Training ids a tester may start: they exist and have a version the agent can run."""
    ids = list(dict.fromkeys(training_ids))
    rows = db.execute(
        select(trainings.c.training_id).where(
            trainings.c.training_id.in_(ids), trainings.c.active_version_id.is_not(None)
        )
    ).all()
    found = {r.training_id for r in rows}
    missing = [t for t in ids if t not in found]
    if missing:
        raise ApiError(422, "unknown_training", "These trainings don't exist or aren't published yet.",
                       {"trainings": missing})  # fmt: skip
    return ids


def tester_view(db: Session, tester: Tester) -> dict[str, Any]:
    by = db.get(DashUser, tester.created_by).full_name if tester.created_by else None  # type: ignore[union-attr]
    return {
        "id": tester.id,
        "uid": tester.uid,
        "name": tester.name,
        "trainings": list(_json(tester.trainings) or []),
        "is_active": tester.is_active,
        "created_by": by,
        "created_at": tester.created_at,
        "code_changed_at": tester.code_changed_at,
    }


def list_testers(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(Tester).order_by(Tester.is_active.desc(), Tester.name)).all()
    return [tester_view(db, t) for t in rows]


def _tester(db: Session, tester_id: int) -> Tester:
    tester = db.get(Tester, tester_id)
    if tester is None:
        raise ApiError(404, "not_found", "Tester not found.")
    return tester


def create_tester(db: Session, current: CurrentUser, data: dict[str, Any], ip: str | None) -> dict[str, Any]:
    if db.scalars(select(Tester.id).where(Tester.uid == data["uid"])).first():
        raise ApiError(409, "tester_exists", "There's already a tester with that uid.")
    code = new_code()
    tester = Tester(
        uid=data["uid"],
        name=data["name"],
        code_hash=code_hash(code),
        trainings=_runnable(db, data["trainings"]),
        is_active=True,
        created_by=current.user.id,
    )
    db.add(tester)
    db.flush()
    audit.record(db, AuditAction.TESTER_CHANGED, actor_user_id=current.user.id, target_type="tester",
                 target_id=tester.id, details={"created": True, "uid": tester.uid}, ip=ip)  # fmt: skip
    db.commit()
    return {"tester": tester_view(db, tester), "code": code}


def update_tester(
    db: Session, current: CurrentUser, tester_id: int, changes: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    tester = _tester(db, tester_id)
    if "trainings" in changes:
        tester.trainings = _runnable(db, changes["trainings"])
    if "name" in changes:
        tester.name = changes["name"]
    if "is_active" in changes:
        tester.is_active = changes["is_active"]
    audit.record(db, AuditAction.TESTER_CHANGED, actor_user_id=current.user.id, target_type="tester",
                 target_id=tester.id, details={"fields": sorted(changes)}, ip=ip)  # fmt: skip
    db.commit()
    return tester_view(db, tester)


def reset_code(db: Session, current: CurrentUser, tester_id: int, ip: str | None) -> dict[str, Any]:
    tester = _tester(db, tester_id)
    code = new_code()
    tester.code_hash = code_hash(code)
    tester.code_changed_at = func.utc_timestamp(6)
    audit.record(db, AuditAction.TESTER_CHANGED, actor_user_id=current.user.id, target_type="tester",
                 target_id=tester.id, details={"new_code": True}, ip=ip)  # fmt: skip
    db.commit()
    db.refresh(tester)
    return {"tester": tester_view(db, tester), "code": code}


def import_testers(
    db: Session, current: CurrentUser, entries: list[dict[str, Any]], ip: str | None
) -> dict[str, Any]:
    """Testers from the agent's testers.json, hashes as they are: everyone keeps their code. Testers already
    in the table (same uid) are left alone; unknown trainings are dropped from their list and reported."""
    added, skipped, dropped = [], [], {}
    known = {
        r.training_id
        for r in db.execute(
            select(trainings.c.training_id).where(trainings.c.active_version_id.is_not(None))
        ).all()
    }
    for entry in entries:
        if db.scalars(select(Tester.id).where(Tester.uid == entry["uid"])).first():
            skipped.append(entry["uid"])
            continue
        wanted = list(dict.fromkeys(entry["trainings"]))
        keep = [t for t in wanted if t in known]
        if len(keep) != len(wanted):
            dropped[entry["uid"]] = [t for t in wanted if t not in known]
        db.add(Tester(uid=entry["uid"], name=entry["name"], code_hash=entry["code_sha256"], trainings=keep,
                      is_active=True, created_by=current.user.id))  # fmt: skip
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise ApiError(
                409, "duplicate_code", "Two testers share an access code; nothing was imported."
            ) from exc
        added.append(entry["uid"])
    audit.record(db, AuditAction.TESTER_CHANGED, actor_user_id=current.user.id, target_type="tester",
                 details={"imported": len(added), "skipped": len(skipped)}, ip=ip)  # fmt: skip
    db.commit()
    return {"added": added, "skipped": skipped, "dropped_trainings": dropped}


def is_hash(value: str) -> bool:
    return bool(_HASH.match(value))
