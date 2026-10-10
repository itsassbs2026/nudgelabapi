"""Who the trainer is for one employee and training: the name it uses and the voice it speaks with.

`trainer_persona` is the one place this is decided. The app's list shows its result (`trainer_person_name`,
`trainer_voice`), and a session start accepts back only what it offered, or the training's own default name.
Changing the rule later (a persona table, per-training voices) means changing this module only: the app sends
back whatever the list gave it.

Today:
  name   the employee's district manager's full name (vw_app_profile), or none
  voice  the voice the agent would pick anyway: the training's own coach voice (`training.voice` in the
         version's content, chosen in the studio) if it's active, else the setup's voice, else the default
         voice (training_voices.default_marker)

The trainer says only the first name ("Akbar Mohamed" → "Akbar"). Spoken names are letters with single spaces,
hyphens or apostrophes between them, at most 40 characters: nothing else reaches the trainer's instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import training_profiles, training_versions, training_voices
from app.utils.errors import ApiError

DEFAULT_TRAINER_NAME = "Anne"  # the agent's trainings all use it; the content's own name wins when present
_NAME = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
MAX_SPOKEN = 40


@dataclass(frozen=True)
class Persona:
    name: str | None  # as shown in the app (a full name)
    voice: str | None


def spoken_name(name: str | None) -> str | None:
    """The first name, if it's a plain name; None otherwise (then the training's default is used)."""
    if not name or not name.strip():
        return None
    first = name.strip().split()[0].strip(".,")
    return first if len(first) <= MAX_SPOKEN and _NAME.match(first) else None


class Voices:
    """Active voices and the default each training resolves to, read once per request."""

    def __init__(self, db: Session) -> None:
        rows = db.execute(
            select(training_voices.c.voice_id, training_voices.c.is_default).where(
                training_voices.c.is_active == 1
            )
        ).all()
        self.active = {str(r.voice_id).lower(): str(r.voice_id) for r in rows}
        self.default = next((str(r.voice_id) for r in rows if r.is_default), None)
        profiles = db.execute(
            select(
                training_profiles.c.profile_id, training_profiles.c.voice_id, training_profiles.c.is_default
            )
        ).all()
        self._profile_voice = {r.profile_id: r.voice_id for r in profiles}
        self._default_profile = next((r.profile_id for r in profiles if r.is_default), None)

    def canonical(self, voice: str | None) -> str | None:
        return self.active.get(voice.lower()) if voice else None

    def for_training(self, profile_id: str | None, content_voice: str | None = None) -> str | None:
        """As the agent picks: the coach voice, else the setup's, else the default (active voices only)."""
        setup = profile_id if profile_id in self._profile_voice else self._default_profile
        chosen = self.canonical(content_voice) or self.canonical(self._profile_voice.get(setup))
        return chosen or self.default


def trainer_persona(
    profile: dict[str, Any], profile_id: str | None, voices: Voices, content_voice: str | None = None
) -> Persona:
    return Persona(
        name=profile.get("district_manager_name") or None,
        voice=voices.for_training(profile_id, content_voice),
    )


def _training_setting(db: Session, version_ids: set[int], key: str) -> dict[int, str]:
    """`training.<key>` from each version's content, where it's set."""
    if not version_ids:
        return {}
    value = func.json_unquote(func.json_extract(training_versions.c.content, f"$.training.{key}"))
    rows = db.execute(
        select(training_versions.c.version_id, value.label("value")).where(
            training_versions.c.version_id.in_(version_ids)
        )
    ).all()
    return {int(r.version_id): r.value for r in rows if r.value and r.value != "null"}


def default_names(db: Session, version_ids: set[int]) -> dict[int, str]:
    """Each version's own trainer name (`training.trainer_name` in its content), or "Anne"."""
    found = _training_setting(db, version_ids, "trainer_name")
    return {v: found.get(v, DEFAULT_TRAINER_NAME) for v in version_ids}


def content_voices(db: Session, version_ids: set[int]) -> dict[int, str]:
    """Each version's own coach voice (`training.voice` in its content), where one is chosen."""
    return _training_setting(db, version_ids, "voice")


def session_version(row: Any) -> int:
    """The version a session will run: a trainee mid-training stays on theirs (the agent's rule)."""
    pinned = row.progress_version_id and row.first_started_at is not None and row.passed_at is None
    return int(row.progress_version_id if pinned else row.active_version_id)


@dataclass(frozen=True)
class Chosen:
    display_name: str
    spoken: str | None  # None: let the agent use the content's own name
    voice: str | None


def choose(
    *,
    persona: Persona,
    default_name: str,
    voices: Voices,
    requested_name: str | None,
    requested_voice: str | None,
) -> Chosen:
    """What the session uses. The app may only send back what it was offered."""
    allowed = {n.strip().casefold(): n.strip() for n in (persona.name, default_name) if n and n.strip()}
    if requested_name is not None:
        name = allowed.get(requested_name.strip().casefold())
        if name is None:
            raise ApiError(
                422, "trainer_name_not_allowed", "That trainer name isn't one offered for this training."
            )
    else:
        name = (persona.name or default_name).strip()
    if requested_voice is not None:
        voice = voices.canonical(requested_voice)
        if voice is None:
            raise ApiError(422, "trainer_voice_not_allowed", "That voice isn't available.")
    else:
        voice = persona.voice
    return Chosen(display_name=name, spoken=spoken_name(name) or spoken_name(default_name), voice=voice)
