"""Autosave history (2026-10-22): a draft keeps earlier saves to go back to. Snapshots are taken before a
save overwrites the content, at most one per studio.history_min_minutes and the newest
studio.history_keep per draft (Admin > Settings; 0 keep = off); restoring one is a save (with the usual
conflict check) that snapshots what was there; and history can never make a save fail.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.services import settings_table
from app.studio import history
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import seed
from tests.test_studio import big4_content, new_draft

V = "/api/v1/versions"


@pytest.fixture
def studio_data(db_session: Session) -> None:
    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(big4_content())},
    )
    settings_table.clear_cache()


def setting(db: Session, key: str, value: int) -> None:
    db.execute(text("UPDATE dash_settings SET value = :v WHERE `key` = :k"), {"v": str(value), "k": key})
    settings_table.clear_cache()


def save(client: TestClient, headers: dict[str, str], draft: dict[str, Any], closing: str) -> dict[str, Any]:
    """Save the draft with its Closing line set to `closing`; returns the new summary."""
    got = client.get(f"{V}/{draft['version_id']}/content", headers=headers).json()
    content = got["content"]
    content["training"]["lines"]["closing"] = closing
    r = client.put(
        f"{V}/{draft['version_id']}/content",
        headers=headers,
        json={"revision": got["revision"], "content": content},
    )
    assert r.status_code == 200, r.text
    return r.json()


def closing_of(client: TestClient, headers: dict[str, str], vid: int) -> str:
    return client.get(f"{V}/{vid}/content", headers=headers).json()["content"]["training"]["lines"]["closing"]


def entries(client: TestClient, headers: dict[str, str], vid: int) -> list[dict[str, Any]]:
    r = client.get(f"{V}/{vid}/history", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_for_the_editor(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    got = client.get("/api/v1/studio-settings", headers=trainer_headers).json()
    assert got == {
        "autosave_seconds": 20,
        "history_keep": 20,
        "history_min_minutes": 10,
        "stale_draft_days": 60,
    }


def test_the_first_save_keeps_the_content_it_replaced_and_later_ones_wait(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    save(client, trainer_headers, draft, "First change.")
    save(client, trainer_headers, draft, "Second change.")  # within 10 minutes: no second snapshot
    saves = entries(client, trainer_headers, vid)
    assert len(saves) == 1
    assert saves[0]["summary"]["topics"] >= 1 and saves[0]["saved_by"]
    assert closing_of(client, trainer_headers, vid) == "Second change."


def test_one_snapshot_per_interval_and_only_the_newest_are_kept(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    setting(db_session, "studio.history_min_minutes", 0)
    setting(db_session, "studio.history_keep", 3)
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    for n in range(1, 7):
        save(client, trainer_headers, draft, f"Closing {n}.")
    saves = entries(client, trainer_headers, vid)
    assert len(saves) == 3  # the newest three; the rest were trimmed
    assert [s["revision"] for s in saves] == sorted((s["revision"] for s in saves), reverse=True)
    setting(db_session, "studio.history_keep", 0)  # 0 = no history: nothing new is kept
    save(client, trainer_headers, draft, "After switching it off.")
    assert len(entries(client, trainer_headers, vid)) == 3
    setting(db_session, "studio.history_min_minutes", 10)
    setting(db_session, "studio.history_keep", 20)


def test_restoring_an_earlier_save_puts_it_back_and_can_itself_be_undone(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    setting(db_session, "studio.history_min_minutes", 0)
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    original = closing_of(client, trainer_headers, vid)
    save(client, trainer_headers, draft, "A mistake.")
    mistake = save(client, trainer_headers, draft, "A bigger mistake.")
    oldest = entries(client, trainer_headers, vid)[-1]  # the content before the first save
    restored = client.post(
        f"{V}/{vid}/history/{oldest['id']}/restore",
        headers=trainer_headers,
        json={"revision": mistake["revision"]},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == mistake["revision"] + 1
    assert closing_of(client, trainer_headers, vid) == original
    # What was there is kept, so the restore can be undone: the newest entry holds "A bigger mistake."
    newest = entries(client, trainer_headers, vid)[0]
    again = client.post(
        f"{V}/{vid}/history/{newest['id']}/restore",
        headers=trainer_headers,
        json={"revision": restored.json()["revision"]},
    )
    assert again.status_code == 200
    assert closing_of(client, trainer_headers, vid) == "A bigger mistake."
    log = (
        db_session.execute(
            text("SELECT action FROM dash_audit_log WHERE action = 'version_history_restored'")
        )
        .scalars()
        .all()
    )
    assert len(log) == 2
    setting(db_session, "studio.history_min_minutes", 10)


def test_restore_checks_the_revision_the_draft_and_the_save(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    saved = save(client, trainer_headers, draft, "Changed.")
    entry = entries(client, trainer_headers, vid)[0]
    stale = client.post(
        f"{V}/{vid}/history/{entry['id']}/restore",
        headers=trainer_headers,
        json={"revision": saved["revision"] - 1},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "edit_conflict"
    assert closing_of(client, trainer_headers, vid) == "Changed."
    gone = client.post(
        f"{V}/{vid}/history/999999/restore", headers=trainer_headers, json={"revision": saved["revision"]}
    )
    assert gone.status_code == 404
    other = new_draft(client, trainer_headers)["version_id"]
    wrong = client.post(
        f"{V}/{other}/history/{entry['id']}/restore", headers=trainer_headers, json={"revision": 1}
    )
    assert wrong.status_code == 404  # an entry of another draft
    live = client.post(f"{V}/1/history/{entry['id']}/restore", headers=trainer_headers, json={"revision": 1})
    assert live.status_code == 409 and live.json()["error"]["code"] == "not_a_draft"


def test_history_can_never_make_a_save_fail(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = new_draft(client, trainer_headers)

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the history table is not there")

    monkeypatch.setattr(history, "settings", broken)  # inside the snapshot's guard
    save(client, trainer_headers, draft, "Saved anyway.")
    assert closing_of(client, trainer_headers, draft["version_id"]) == "Saved anyway."
    assert entries(client, trainer_headers, draft["version_id"]) == []
