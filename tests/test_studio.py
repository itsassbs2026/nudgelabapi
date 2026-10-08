"""Training studio API (SPEC 8.3, Phase 11): trainings, versions, content with optimistic locking, diffs.

Acceptance (SPEC §15): two editors can't overwrite each other silently.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from app.schemas.training_content import TrainingContent
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import seed

FIXTURES = Path(__file__).parent / "fixtures" / "content"


def big4_content() -> dict[str, Any]:
    return json.loads((FIXTURES / "big4.json").read_text(encoding="utf-8"))


@pytest.fixture
def studio_data(db_session: Session) -> None:
    """seed(): big4 (v1 active) and walk (v2 active). Give big4's live version real content."""
    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(big4_content())},
    )


def agent_view(db: Session) -> list[tuple[Any, ...]]:
    """What the voice agent reads: each training's active version and that version's content."""
    rows = db.execute(
        text(
            "SELECT t.training_id, t.active_version_id, v.content FROM trainings t "
            "LEFT JOIN training_versions v ON v.version_id = t.active_version_id ORDER BY t.training_id"
        )
    ).all()
    return [tuple(r) for r in rows]


def new_draft(client: TestClient, headers: dict[str, str], source: int = 1) -> dict[str, Any]:
    r = client.post(
        "/api/v1/trainings/big4/versions",
        headers=headers,
        json={"source": "version", "source_version_id": source},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- Creating a training ---------------------------------------------------------------------------------


def test_create_training(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    r = client.post(
        "/api/v1/trainings",
        headers=trainer_headers,
        json={
            "training_id": "return_policy",
            "title": "Return Policy",
            "completion_type": "acknowledgment",
            "trainer_name": "Maya",
            "profile_id": "standard",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["status"], body["active_version_id"], body["completion_type"]) == (
        "draft",
        None,
        "acknowledgment",
    )
    (version,) = body["versions"]
    assert (version["status"], version["revision"], version["created_by"]) == ("draft", 1, "User 1")

    content = client.get(f"/api/v1/versions/{version['version_id']}/content", headers=trainer_headers).json()
    training = content["content"]["training"]
    assert training["trainer_name"] == "Maya"
    assert training["acknowledgment"] == ""
    assert set(training["lines"]) == {
        "first_message", "already_passed", "feedback_question", "closing", "welcome_back_walkthrough",
        "completed", "acknowledgment_intro", "welcome_back_acknowledgment",
    }  # fmt: skip
    assert all(v == "" for v in training["lines"].values())  # nothing invented
    assert content["content"]["quiz"] is None
    TrainingContent.model_validate(content["content"])  # the agent's format

    action = db_session.execute(
        text("SELECT action FROM dash_audit_log WHERE target_id = 'return_policy'")
    ).scalar_one()
    assert action == "training_created"


def test_create_quiz_training_with_location(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    body = client.post(
        "/api/v1/trainings",
        headers=trainer_headers,
        json={"training_id": "fiber_basics", "title": "Fiber Basics", "uses_location": True},
    ).json()
    content = client.get(
        f"/api/v1/versions/{body['versions'][0]['version_id']}/content", headers=trainer_headers
    ).json()["content"]
    assert content["quiz"] == {"sections": {}, "questions": []}
    assert {"quiz_intro", "perfect_score", "location_unknown"} <= set(content["training"]["lines"])


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"training_id": "big4", "title": "Again"}, 409, "training_exists"),
        ({"training_id": "Big-4", "title": "Bad id"}, 422, "validation_error"),
        ({"training_id": "ab", "title": "Too short"}, 422, "validation_error"),
        ({"training_id": "nice_id", "title": "   "}, 422, "validation_error"),
        ({"training_id": "nice_id", "title": "X", "profile_id": "nope"}, 422, "unknown_profile"),
        ({"training_id": "nice_id", "title": "X", "completion_key": "has spaces"}, 422, "validation_error"),
        ({"training_id": "nice_id", "title": "X", "active_version_id": 1}, 422, "validation_error"),
    ],
)
def test_create_training_refused(
    client: TestClient,
    trainer_headers: dict[str, str],
    studio_data: None,
    payload: dict[str, Any],
    status: int,
    code: str,
) -> None:
    r = client.post("/api/v1/trainings", headers=trainer_headers, json=payload)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code


def test_list_and_detail(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    new_draft(client, trainer_headers)
    items = {t["training_id"]: t for t in client.get("/api/v1/trainings", headers=trainer_headers).json()}
    assert items["big4"]["active_version"] == {"version_id": 1, "label": "v1"}
    assert (items["big4"]["drafts"], items["walk"]["drafts"]) == (1, 0)

    detail = client.get("/api/v1/trainings/big4", headers=trainer_headers).json()
    statuses = [(v["status"], v["is_active"]) for v in detail["versions"]]
    assert statuses == [("draft", False), ("published", True)]  # newest first
    walk = client.get("/api/v1/trainings/walk", headers=trainer_headers).json()
    assert walk["versions"][0]["status"] == "published"  # legacy row without a status, active
    assert client.get("/api/v1/trainings/nope_nope", headers=trainer_headers).status_code == 404


# --- Settings --------------------------------------------------------------------------------------------


def test_trainer_edits_settings(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    r = client.patch(
        "/api/v1/trainings/big4",
        headers=trainer_headers,
        json={
            "title": "The Big 4 (2026)",
            "category": " Sales ",
            "is_required": False,
            "completion_key": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["title"], body["category"], body["is_required"], body["completion_key"]) == (
        "The Big 4 (2026)",
        "Sales",
        False,
        None,
    )


@pytest.mark.parametrize("change", [{"archived": True}, {"app_status": "archived"}])
def test_archiving_is_admin_only(
    client: TestClient,
    trainer_headers: dict[str, str],
    admin_headers: dict[str, str],
    studio_data: None,
    change: dict[str, Any],
) -> None:
    assert client.patch("/api/v1/trainings/big4", headers=trainer_headers, json=change).status_code == 403
    assert client.patch("/api/v1/trainings/big4", headers=admin_headers, json=change).status_code == 200


def test_archive_and_restore(client: TestClient, admin_headers: dict[str, str], studio_data: None) -> None:
    archived = client.patch("/api/v1/trainings/big4", headers=admin_headers, json={"archived": True}).json()
    assert archived["status"] == "retired"
    assert archived["active_version_id"] == 1  # archiving doesn't touch what's published
    restored = client.patch("/api/v1/trainings/big4", headers=admin_headers, json={"archived": False}).json()
    assert restored["status"] == "active"
    client.post("/api/v1/trainings", headers=admin_headers, json={"training_id": "never_live", "title": "N"})
    client.patch("/api/v1/trainings/never_live", headers=admin_headers, json={"archived": True})
    back = client.patch(
        "/api/v1/trainings/never_live", headers=admin_headers, json={"archived": False}
    ).json()
    assert back["status"] == "draft"  # never published: back to draft, not active


def test_type_is_locked_once_published(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    r = client.patch(
        "/api/v1/trainings/big4", headers=trainer_headers, json={"completion_type": "walkthrough"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "locked_after_publish"
    client.post("/api/v1/trainings", headers=trainer_headers, json={"training_id": "draft_one", "title": "D"})
    ok = client.patch(
        "/api/v1/trainings/draft_one", headers=trainer_headers, json={"completion_type": "walkthrough"}
    )
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"title": None},
        {"title": "  "},
        {"profile_id": "nope"},
        {"active_version_id": 2},
        {"status": "active"},
    ],
)
def test_bad_settings(
    client: TestClient, admin_headers: dict[str, str], studio_data: None, change: dict[str, Any]
) -> None:
    assert client.patch("/api/v1/trainings/big4", headers=admin_headers, json=change).status_code == 422


# --- Versions and content --------------------------------------------------------------------------------


def test_copy_a_version(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    draft = new_draft(client, trainer_headers)
    assert (draft["status"], draft["revision"], draft["label"].startswith("Draft ")) == ("draft", 1, True)
    content = client.get(f"/api/v1/versions/{draft['version_id']}/content", headers=trainer_headers).json()
    assert content["content"] == big4_content()


def test_copy_refused(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    url = "/api/v1/trainings/big4/versions"
    other = client.post(url, headers=trainer_headers, json={"source": "version", "source_version_id": 2})
    assert other.json()["error"]["code"] == "wrong_training"  # version 2 is walk's
    empty = client.post(
        "/api/v1/trainings/walk/versions",
        headers=trainer_headers,
        json={"source": "version", "source_version_id": 2},
    )
    assert empty.json()["error"]["code"] == "no_content"
    missing = client.post(url, headers=trainer_headers, json={"source": "version"})
    assert missing.status_code == 422


def test_blank_version(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    r = client.post(
        "/api/v1/trainings/walk/versions",
        headers=trainer_headers,
        json={"source": "blank", "label": "Rewrite"},
    )
    assert r.status_code == 201
    content = client.get(f"/api/v1/versions/{r.json()['version_id']}/content", headers=trainer_headers).json()
    assert content["label"] == "Rewrite"
    assert "completed" in content["content"]["training"]["lines"]  # walkthrough lines


def test_save_content(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    draft = new_draft(client, trainer_headers)
    url = f"/api/v1/versions/{draft['version_id']}/content"
    content = big4_content()
    content["knowledge_base"]["topics"][1]["lines"][0]["text"] = "A new first line."
    saved = client.put(url, headers=trainer_headers, json={"revision": 1, "content": content})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["revision"], saved.json()["updated_by"]) == (2, "User 1")
    assert client.get(url, headers=trainer_headers).json()["content"] == content  # stored exactly as sent


def test_two_editors_cant_overwrite_each_other(
    client: TestClient,
    make_user: Any,
    login: Any,
    studio_data: None,
) -> None:
    """SPEC §15 Phase 11 acceptance."""
    alice = login(make_user(email="alice@example.com").email)
    bob = login(make_user(email="bob@example.com").email)
    draft = new_draft(client, alice)
    url = f"/api/v1/versions/{draft['version_id']}/content"

    # Both open the draft at revision 1.
    alice_copy = client.get(url, headers=alice).json()
    bob_copy = client.get(url, headers=bob).json()
    assert alice_copy["revision"] == bob_copy["revision"] == 1

    alice_content = copy.deepcopy(alice_copy["content"])
    alice_content["training"]["lines"]["closing"] = "Alice's closing."
    assert client.put(url, headers=alice, json={"revision": 1, "content": alice_content}).status_code == 200

    bob_content = copy.deepcopy(bob_copy["content"])
    bob_content["training"]["lines"]["closing"] = "Bob's closing."
    refused = client.put(url, headers=bob, json={"revision": 1, "content": bob_content})
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "edit_conflict"
    assert error["details"]["current_revision"] == 2
    assert error["details"]["updated_by"] == "User 1"  # alice
    assert (
        client.get(url, headers=bob).json()["content"]["training"]["lines"]["closing"] == "Alice's closing."
    )

    # Bob reloads, applies his change on top, and saves.
    latest = client.get(url, headers=bob).json()
    latest["content"]["training"]["lines"]["closing"] = "Bob's closing."
    ok = client.put(url, headers=bob, json={"revision": latest["revision"], "content": latest["content"]})
    assert (ok.status_code, ok.json()["revision"]) == (200, 3)


def test_only_drafts_can_be_edited(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    r = client.put(
        "/api/v1/versions/1/content", headers=trainer_headers, json={"revision": 1, "content": big4_content()}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_a_draft"


@pytest.mark.parametrize(
    "breakage",
    [
        lambda c: c.pop("knowledge_base"),
        lambda c: c.update(format=2),
        lambda c: c["knowledge_base"]["topics"][0]["lines"][0].update(kind="shout"),
        lambda c: c["quiz"]["questions"][0].update(correct="E"),
        lambda c: c.update(extra_field=True),
    ],
)
def test_invalid_content_is_refused(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None, breakage: Any
) -> None:
    draft = new_draft(client, trainer_headers)
    content = big4_content()
    breakage(content)
    r = client.put(
        f"/api/v1/versions/{draft['version_id']}/content",
        headers=trainer_headers,
        json={"revision": 1, "content": content},
    )
    assert r.status_code == 422


def test_drafts_never_reach_the_agent(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session, studio_data: None
) -> None:
    before = agent_view(db_session)
    draft = new_draft(client, trainer_headers)
    content = big4_content()
    content["training"]["title"] = "Changed in a draft"
    client.put(f"/api/v1/versions/{draft['version_id']}/content", headers=trainer_headers,
               json={"revision": 1, "content": content})  # fmt: skip
    client.patch("/api/v1/trainings/big4", headers=trainer_headers, json={"category": "Sales"})
    assert agent_view(db_session) == before
    hashes = db_session.execute(text("SELECT content_hash FROM training_versions")).scalars().all()
    assert len(set(hashes)) == len(hashes)  # unique placeholder hashes


# --- Diff ------------------------------------------------------------------------------------------------


def test_diff(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    draft = new_draft(client, trainer_headers)
    vid = draft["version_id"]
    same = client.get(f"/api/v1/versions/1/diff/{vid}", headers=trainer_headers).json()
    assert same["identical"] is True

    content = big4_content()
    topic = content["knowledge_base"]["topics"][1]
    old_line = topic["lines"][0]["prefix"] + topic["lines"][0]["text"]
    topic["lines"][0]["text"] = "Rewritten."
    content["knowledge_base"]["topics"].pop()  # remove the last topic
    content["training"]["lines"]["closing"] = "Bye for now."
    content["training"]["title"] = "The Big 4, revised"
    content["vocabulary"] = [*content["vocabulary"], "Fiber-Plus"]
    content["quiz"]["questions"][0]["correct"] = (
        "C" if content["quiz"]["questions"][0].get("correct") != "C" else "A"
    )
    client.put(
        f"/api/v1/versions/{vid}/content", headers=trainer_headers, json={"revision": 1, "content": content}
    )

    d = client.get(f"/api/v1/versions/1/diff/{vid}", headers=trainer_headers).json()
    assert d["identical"] is False
    assert (d["from"]["version_id"], d["to"]["version_id"]) == (1, vid)
    assert d["settings"] == [{"key": "title", "from": "The Big 4", "to": "The Big 4, revised"}]
    assert [c["key"] for c in d["lines"]] == ["closing"]
    changed = {t["number"]: t for t in d["topics"]}
    assert changed[2]["change"] == "changed"
    assert changed[2]["lines"] == [
        {"op": "replace", "from": [old_line], "to": [topic["lines"][0]["prefix"] + "Rewritten."]}
    ]
    assert changed[16]["change"] == "removed"
    assert d["vocabulary"] == {"added": ["Fiber-Plus"], "removed": []}
    assert [q["number"] for q in d["quiz"]["questions"]] == [1]


def test_diff_refused(client: TestClient, trainer_headers: dict[str, str], studio_data: None) -> None:
    assert (
        client.get("/api/v1/versions/1/diff/2", headers=trainer_headers).json()["error"]["code"]
        == "wrong_training"
    )
    assert client.get("/api/v1/versions/1/diff/999", headers=trainer_headers).status_code == 404


# --- Reports don't show studio work ----------------------------------------------------------------------


def test_reports_ignore_drafts(
    client: TestClient, trainer_headers: dict[str, str], studio_data: None
) -> None:
    new_draft(client, trainer_headers)
    client.post(
        "/api/v1/trainings", headers=trainer_headers, json={"training_id": "unreleased", "title": "U"}
    )
    params = {"range": "custom", "from": "2026-09-01", "to": "2026-09-30"}
    rows = client.get("/api/v1/reports/trainings", headers=trainer_headers, params=params).json()
    assert "unreleased" not in json.dumps(rows)
    options = client.get("/api/v1/reports/filter-options", headers=trainer_headers).json()
    assert {t["id"] for t in options["trainings"]} == {"big4", "walk"}  # not the unreleased draft
    detail = client.get("/api/v1/reports/trainings/big4", headers=trainer_headers, params=params).json()
    assert [v["version_id"] for v in detail["versions"]] == [1]
