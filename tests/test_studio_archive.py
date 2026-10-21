"""Archiving drafts and the studio list's tidy-up (2026-10-22): a trainer archives and restores the drafts
they made, an Admin any draft; only drafts can be archived; nothing is deleted; archived drafts can't be
edited and don't count; the list says who edited last; an old untouched draft is flagged
(studio.stale_draft_days)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from app.services import settings_table
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import seed
from tests.test_studio import big4_content, new_draft

V = "/api/v1/versions"


@pytest.fixture
def studio_data(db_session: Session) -> None:
    """big4 (v1 live, with content) and walk (v2 live), as in test_studio.py."""
    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(big4_content())},
    )


def versions(client: TestClient, headers: dict[str, str]) -> dict[int, dict[str, Any]]:
    detail = client.get("/api/v1/trainings/big4", headers=headers).json()
    return {v["version_id"]: v for v in detail["versions"]}


def test_a_trainer_archives_and_restores_their_own_draft(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    archived = client.post(f"{V}/{vid}/archive", headers=trainer_headers)
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    # Nothing is deleted: the content is still there, and it's listed (the dashboard hides it by default).
    assert versions(client, trainer_headers)[vid]["has_content"] is True
    assert (
        db_session.execute(
            text("SELECT status FROM training_versions WHERE version_id = :v"), {"v": vid}
        ).scalar_one()
        == "archived"
    )
    # It no longer counts as a draft in the list.
    big4 = next(
        t
        for t in client.get("/api/v1/trainings", headers=trainer_headers).json()
        if t["training_id"] == "big4"
    )
    assert (big4["drafts"], big4["archived_drafts"]) == (0, 1)
    # Archived drafts can't be edited, submitted or archived twice.
    save = client.put(
        f"{V}/{vid}/content",
        headers=trainer_headers,
        json={
            "revision": draft["revision"],
            "content": client.get(f"{V}/{vid}/content", headers=trainer_headers).json()["content"],
        },
    )
    assert save.status_code == 409 and save.json()["error"]["code"] == "not_a_draft"
    assert client.post(f"{V}/{vid}/submit", headers=trainer_headers).status_code == 409
    assert client.post(f"{V}/{vid}/archive", headers=trainer_headers).json()["error"]["code"] == "not_a_draft"
    restored = client.post(f"{V}/{vid}/restore", headers=trainer_headers)
    assert restored.status_code == 200 and restored.json()["status"] == "draft"
    assert (
        client.post(f"{V}/{vid}/restore", headers=trainer_headers).json()["error"]["code"] == "not_archived"
    )
    log = (
        db_session.execute(
            text("SELECT action FROM dash_audit_log WHERE action LIKE 'version_%' ORDER BY id")
        )
        .scalars()
        .all()
    )
    assert log[-2:] == ["version_archived", "version_restored"]


def test_a_trainer_cant_touch_someone_elses_draft_but_an_admin_can(
    client: TestClient,
    trainer_headers: dict[str, str],
    admin_headers: dict[str, str],
    make_user: Callable[..., object],
    login: Callable[..., dict[str, str]],
    studio_data: None,
) -> None:
    mine = new_draft(client, trainer_headers)["version_id"]
    other = login(make_user(role="trainer", email="other@example.com").email)  # type: ignore[attr-defined]
    refused = client.post(f"{V}/{mine}/archive", headers=other)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    assert client.post(f"{V}/{mine}/archive", headers=admin_headers).json()["status"] == "archived"
    assert client.post(f"{V}/{mine}/restore", headers=other).status_code == 403
    assert client.post(f"{V}/{mine}/restore", headers=admin_headers).json()["status"] == "draft"


def test_only_drafts_can_be_archived(
    client: TestClient, admin_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    live = 1  # big4's live version
    refused = client.post(f"{V}/{live}/archive", headers=admin_headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_a_draft"
    draft = new_draft(client, admin_headers)["version_id"]
    db_session.execute(
        text("UPDATE training_versions SET status = 'in_review' WHERE version_id = :v"), {"v": draft}
    )
    assert client.post(f"{V}/{draft}/archive", headers=admin_headers).status_code == 409  # in review: stays
    assert (
        db_session.execute(text("SELECT status FROM training_versions WHERE version_id = 1")).scalar_one()
        == "published"
    )


def test_a_draft_being_prepared_from_a_document_cant_be_archived(
    client: TestClient, admin_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    vid = new_draft(client, admin_headers)["version_id"]
    db_session.execute(
        text(
            "INSERT INTO jobs (type, status, version_id, input, created_at)"
            " VALUES ('prepare_for_voice', 'running', :v, '{}', UTC_TIMESTAMP())"
        ),
        {"v": vid},
    )
    r = client.post(f"{V}/{vid}/archive", headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "being_prepared"


def test_the_list_says_who_edited_last_and_old_drafts_are_flagged(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    draft = new_draft(client, trainer_headers)
    content = client.get(f"{V}/{draft['version_id']}/content", headers=trainer_headers).json()["content"]
    client.put(
        f"{V}/{draft['version_id']}/content",
        headers=trainer_headers,
        json={"revision": draft["revision"], "content": content},
    )
    big4 = next(
        t
        for t in client.get("/api/v1/trainings", headers=trainer_headers).json()
        if t["training_id"] == "big4"
    )
    assert big4["last_edited_by"] and big4["last_edited_by"].startswith("User ")
    fresh = versions(client, trainer_headers)[draft["version_id"]]
    assert fresh["stale"] is False and fresh["created_by_id"] is not None
    old = datetime.now() - timedelta(days=61)
    db_session.execute(
        text("UPDATE training_versions SET updated_at = :t WHERE version_id = :v"),
        {"t": old, "v": draft["version_id"]},
    )
    assert versions(client, trainer_headers)[draft["version_id"]]["stale"] is True
    # The age is an Admin setting; 0 turns the hint off. Live versions are never flagged.
    db_session.execute(text("UPDATE dash_settings SET value = '0' WHERE `key` = 'studio.stale_draft_days'"))
    settings_table.clear_cache()
    assert versions(client, trainer_headers)[draft["version_id"]]["stale"] is False
    assert versions(client, trainer_headers)[1]["stale"] is False
    settings_table.clear_cache()
