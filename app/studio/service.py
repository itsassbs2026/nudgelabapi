"""Trainings and their versions, for the training studio (SPEC 8.3, 10.1–10.2; Phase 11).

What the voice agent relies on, and how this module keeps it safe:
- The agent runs a training's **active version** (`trainings.active_version_id`) or the version a trainee
  started on. It never looks for "the latest" version, so drafts and versions in review are invisible to it.
  Nothing here changes `active_version_id`: publishing is Phase 16.
- `training_versions` is unique on (training, content_hash), and the agent finds versions published from files
  by their file hash. Versions made here get a random hash, fixed at creation, so they can't collide with
  either.
- Only draft content can be saved, and every save names the revision it was based on: a stale save is refused
  (409) instead of overwriting someone else's work.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.models.content import ContentUpload, UploadStatus
from app.models.dashboard import DashUser
from app.reference.agent_tables import training_profiles, training_versions, trainings
from app.schemas.training_content import TrainingContent
from app.services import audit
from app.services.audit import AuditAction
from app.studio import prepare
from app.studio.blank import blank_content
from app.studio.diff import diff
from app.studio.validate import validate
from app.utils.errors import ApiError

DRAFT = "draft"
DEFAULT_TRAINER_NAME = "Anne"
MAX_CONTENT_BYTES = 2_000_000


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


def _version_hash() -> str:
    """A unique placeholder for a version made in the dashboard (see the module docstring)."""
    return hashlib.sha256(b"nudgelab-studio:" + secrets.token_bytes(32)).hexdigest()


def version_status(row: Any, active_version_id: int | None) -> str:
    """Versions published from files before Phase 10 have no status: the active one is published."""
    if row.status:
        return str(row.status)
    return "published" if row.version_id == active_version_id else "retired"


def _training(db: Session, training_id: str) -> Any:
    row = db.execute(select(trainings).where(trainings.c.training_id == training_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "Training not found.")
    return row


def _version(db: Session, version_id: int) -> Any:
    row = db.execute(select(training_versions).where(training_versions.c.version_id == version_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "Version not found.")
    return row


def _names(db: Session, user_ids: set[int]) -> dict[int, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = db.execute(select(DashUser.id, DashUser.full_name).where(DashUser.id.in_(ids))).all()
    return {r.id: r.full_name for r in rows}


# --- Trainings -------------------------------------------------------------------------------------------


def list_trainings(db: Session) -> list[dict[str, Any]]:
    v = training_versions.c
    counts = (
        select(
            v.training_id,
            func.sum(case((v.status == "draft", 1), else_=0)).label("drafts"),
            func.sum(case((v.status == "in_review", 1), else_=0)).label("in_review"),
            func.max(v.updated_at).label("last_edited_at"),
        )
        .group_by(v.training_id)
        .subquery()
    )
    active = training_versions.alias("active")
    rows = db.execute(
        select(
            trainings, counts.c.drafts, counts.c.in_review, counts.c.last_edited_at, active.c.version_label
        )
        .outerjoin(counts, counts.c.training_id == trainings.c.training_id)
        .outerjoin(active, active.c.version_id == trainings.c.active_version_id)
        .order_by(trainings.c.title)
    ).all()
    return [
        {
            **_settings(r),
            "active_version": (
                {"version_id": r.active_version_id, "label": r.version_label} if r.active_version_id else None
            ),
            "drafts": int(r.drafts or 0),
            "in_review": int(r.in_review or 0),
            "last_edited_at": r.last_edited_at,
        }
        for r in rows
    ]


def _settings(r: Any) -> dict[str, Any]:
    return {
        "training_id": r.training_id,
        "title": r.title,
        "status": r.status,
        "completion_type": r.completion_type,
        "uses_location": bool(r.uses_location),
        "completion_key": r.completion_key,
        "profile_id": r.profile_id,
        "app_title": r.app_title,
        "category": r.category,
        "description": r.description,
        "tags": r.tags,
        "is_required": bool(r.is_required),
        "app_status": r.app_status,
        "wanaka_trainer_id": r.wanaka_trainer_id,
    }


def training_detail(db: Session, training_id: str) -> dict[str, Any]:
    t = _training(db, training_id)
    return {
        **_settings(t),
        "active_version_id": t.active_version_id,
        "versions": list_versions(db, training_id),
    }


def _check_profile(db: Session, profile_id: str | None) -> None:
    if profile_id is None:
        return
    found = db.execute(
        select(training_profiles.c.profile_id).where(training_profiles.c.profile_id == profile_id)
    ).first()
    if found is None:
        raise ApiError(422, "unknown_profile", "That setup doesn't exist.", {"profile_id": profile_id})


def create_training(
    db: Session, current: CurrentUser, data: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    exists = db.execute(
        select(trainings.c.training_id).where(trainings.c.training_id == data["training_id"])
    ).first()
    if exists:
        raise ApiError(409, "training_exists", "A training with this id already exists.")
    _check_profile(db, data.get("profile_id"))
    db.execute(
        insert(trainings).values(
            training_id=data["training_id"],
            title=data["title"],
            status=DRAFT,  # not runnable and not in the app until a version is published (Phase 16)
            completion_type=data["completion_type"],
            uses_location=data["uses_location"],
            completion_key=data.get("completion_key"),
            profile_id=data.get("profile_id"),
        )
    )
    content = blank_content(
        title=data["title"],
        completion_type=data["completion_type"],
        uses_location=data["uses_location"],
        trainer_name=data.get("trainer_name") or DEFAULT_TRAINER_NAME,
    )
    version_id = _insert_version(db, current, data["training_id"], content, label=None, notes=None)
    audit.record(
        db,
        AuditAction.TRAINING_CREATED,
        actor_user_id=current.user.id,
        target_type="training",
        target_id=data["training_id"],
        details={"version_id": version_id},
        ip=ip,
    )
    db.commit()
    return training_detail(db, data["training_id"])


# The columns the API may UPDATE: also the column-level grants in deploy/db-grants-0006-studio.sql (tested).
TRAINING_UPDATABLE = frozenset(
    {"title", "status", "completion_type", "uses_location", "completion_key", "profile_id", "app_title",
     "category", "description", "tags", "is_required", "app_status"}
)  # fmt: skip
VERSION_UPDATABLE = frozenset({"content", "revision", "updated_at", "updated_by"})
ADMIN_ONLY = {"archived", "app_status"}
LOCKED_AFTER_PUBLISH = {"completion_type", "uses_location"}


def update_training(
    db: Session, current: CurrentUser, training_id: str, changes: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    t = _training(db, training_id)
    if ADMIN_ONLY & set(changes) and not current.is_admin:
        raise ApiError(403, "forbidden", "Only an Admin can archive a training or hide it from the app.")
    locked = LOCKED_AFTER_PUBLISH & set(changes)
    if locked and t.active_version_id is not None:
        raise ApiError(
            409,
            "locked_after_publish",
            "The completion type and location setting can't change once a version is published.",
            {"fields": sorted(locked)},
        )
    if "profile_id" in changes:
        _check_profile(db, changes["profile_id"])
    values = {k: v for k, v in changes.items() if k != "archived"}
    if "archived" in changes:
        if changes["archived"]:
            values["status"] = "retired"
        else:
            values["status"] = "active" if t.active_version_id is not None else DRAFT
    if values:
        db.execute(update(trainings).where(trainings.c.training_id == training_id).values(**values))
    audit.record(
        db,
        AuditAction.TRAINING_UPDATED,
        actor_user_id=current.user.id,
        target_type="training",
        target_id=training_id,
        details={"fields": sorted(changes)},
        ip=ip,
    )
    db.commit()
    return training_detail(db, training_id)


# --- Versions --------------------------------------------------------------------------------------------


def _version_summary(row: Any, active_version_id: int | None, names: dict[int, str]) -> dict[str, Any]:
    return {
        "version_id": row.version_id,
        "training_id": row.training_id,
        "label": row.version_label,
        "status": version_status(row, active_version_id),
        "is_active": row.version_id == active_version_id,
        "revision": row.revision,
        "has_content": row.content is not None,
        "notes": row.notes,
        "created_at": row.created_at,
        "created_by": names.get(row.created_by) if row.created_by else None,
        "updated_at": row.updated_at,
        "updated_by": names.get(row.updated_by) if row.updated_by else None,
        "published_at": row.published_at if row.status not in ("draft", "in_review") else None,
        "source_upload_id": row.source_upload_id,
    }


def list_versions(db: Session, training_id: str) -> list[dict[str, Any]]:
    t = _training(db, training_id)
    rows = db.execute(
        select(training_versions)
        .where(training_versions.c.training_id == training_id)
        .order_by(training_versions.c.version_id.desc())
    ).all()
    names = _names(db, {r.created_by for r in rows} | {r.updated_by for r in rows})
    return [_version_summary(r, t.active_version_id, names) for r in rows]


def _insert_version(
    db: Session,
    current: CurrentUser,
    training_id: str,
    content: dict[str, Any],
    label: str | None,
    notes: str | None,
    source_upload_id: int | None = None,
) -> int:
    now = _now()
    result = db.execute(
        insert(training_versions).values(
            training_id=training_id,
            version_label=label or f"Draft {now:%Y-%m-%d %H:%M}",
            content_hash=_version_hash(),
            status=DRAFT,
            content=content,
            notes=notes,
            created_by=current.user.id,
            created_at=now,
            revision=1,
            source_upload_id=source_upload_id,
        )
    )
    return int(result.inserted_primary_key[0])  # type: ignore[attr-defined]


def create_version(
    db: Session, current: CurrentUser, training_id: str, data: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    t = _training(db, training_id)
    upload_id = None
    if data["source"] == "version":
        source = _version(db, data["source_version_id"])
        if source.training_id != training_id:
            raise ApiError(422, "wrong_training", "That version belongs to another training.")
        if source.content is None:
            raise ApiError(422, "no_content", "That version has no content to copy.")
        content = _json(source.content)
    else:
        if data["source"] == "upload":
            upload = db.get(ContentUpload, data["upload_id"])
            if upload is None or upload.training_id != training_id:
                raise ApiError(422, "wrong_upload", "That document wasn't uploaded for this training.")
            if upload.status != UploadStatus.READY.value:
                raise ApiError(409, "upload_not_ready", "The document's text isn't ready yet.")
            upload_id = upload.id
        content = blank_content(
            title=t.title,
            completion_type=t.completion_type,
            uses_location=bool(t.uses_location),
            trainer_name=DEFAULT_TRAINER_NAME,
        )
    version_id = _insert_version(
        db, current, training_id, content, data.get("label"), data.get("notes"), source_upload_id=upload_id
    )
    audit.record(
        db,
        AuditAction.VERSION_CREATED,
        actor_user_id=current.user.id,
        target_type="training",
        target_id=training_id,
        details={
            "version_id": version_id,
            "source": data["source"],
            "source_version_id": data.get("source_version_id"),
            "upload_id": upload_id,
        },
        ip=ip,
    )
    db.commit()
    if upload_id is not None:  # made from a document: prepare it for voice straight away (worker job)
        prepare.queue(db, current, version_id)
    return version_summary(db, version_id)


def version_summary(db: Session, version_id: int) -> dict[str, Any]:
    row = _version(db, version_id)
    t = _training(db, row.training_id)
    return _version_summary(row, t.active_version_id, _names(db, {row.created_by, row.updated_by}))


def get_content(db: Session, version_id: int) -> dict[str, Any]:
    row = _version(db, version_id)
    return {**version_summary(db, version_id), "content": _json(row.content)}


def save_content(
    db: Session, current: CurrentUser, version_id: int, revision: int, content: TrainingContent
) -> dict[str, Any]:
    row = _version(db, version_id)
    if row.status != DRAFT:
        raise ApiError(409, "not_a_draft", "Only a draft can be edited. Make a new draft from this version.")
    document = without_nulls(content.model_dump(mode="json", exclude_unset=True, by_alias=True))
    if len(json.dumps(document, ensure_ascii=False).encode()) > MAX_CONTENT_BYTES:
        raise ApiError(413, "content_too_large", "The training is too large to save.")
    v = training_versions.c
    result = db.execute(
        update(training_versions)
        .where(v.version_id == version_id, v.revision == revision, v.status == DRAFT)
        .values(content=document, revision=v.revision + 1, updated_at=_now(), updated_by=current.user.id)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        latest = _version(db, version_id)
        names = _names(db, {latest.updated_by})
        raise ApiError(
            409,
            "edit_conflict",
            "Someone else saved this draft since you opened it. Reload to see their changes.",
            {
                "current_revision": latest.revision,
                "updated_by": names.get(latest.updated_by),
                "updated_at": latest.updated_at.isoformat() if latest.updated_at else None,
                "status": latest.status,
            },
        )
    db.commit()
    return version_summary(db, version_id)


def without_nulls(document: dict[str, Any]) -> dict[str, Any]:
    """Drop explicit nulls where the agent reads "missing" and "null" differently.

    In training.json, a missing `completion_type` means "quiz" but a null one is an error to the agent; the
    same goes for the other settings, a line's `locations`, and a question's fields when it has `variants`.
    Only the top-level `quiz` and `vocabulary` keep null: it means "none", as the agent's export writes them.
    """
    training = document.get("training")
    if isinstance(training, dict):
        document["training"] = {k: v for k, v in training.items() if v is not None}
    knowledge_base = document.get("knowledge_base") or {}
    lines = [*knowledge_base.get("preamble", [])]
    for topic in knowledge_base.get("topics", []):
        lines.extend(topic.get("lines", []))
    for line in lines:
        if line.get("locations") is None:
            line.pop("locations", None)
    for question in (document.get("quiz") or {}).get("questions", []):
        for key in [k for k, v in question.items() if v is None]:
            del question[key]
    return document


def validate_version(db: Session, version_id: int) -> dict[str, Any]:
    row = _version(db, version_id)
    if row.content is None:
        raise ApiError(422, "no_content", "This version has no content to check.")
    training = _training(db, row.training_id)
    return validate(_json(row.content), completion_key=training.completion_key)


def version_diff(db: Session, from_id: int, to_id: int) -> dict[str, Any]:
    a, b = _version(db, from_id), _version(db, to_id)
    if a.training_id != b.training_id:
        raise ApiError(422, "wrong_training", "Both versions must belong to the same training.")
    if a.content is None or b.content is None:
        raise ApiError(422, "no_content", "Both versions need content to compare.")
    return {
        "from": version_summary(db, from_id),
        "to": version_summary(db, to_id),
        **diff(_json(a.content), _json(b.content)),
    }
