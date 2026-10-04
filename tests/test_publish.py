"""Publishing (SPEC 10.1, Phase 16): submit, send back, publish by a worker job, rollback. Transcribe faked.

Acceptance (SPEC §15): publishing a new Big 4 version doesn't disturb in-progress trainees. Pinning is the
agent's (choose_version, tested there); here: publishing switches only `active_version_id` and never touches
training_progress, so a trainee's pinned version_id stays as it was.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from app.config import get_settings
from app.studio import publish, service
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import insert, seed
from tests.test_studio import big4_content, new_draft

Headers = dict[str, str]


@pytest.fixture
def studio(db_session: Session) -> None:
    """big4 v1 is live with real content (and 3 topic rows from seed()); walk v2 is live without content."""
    seed(db_session)
    db_session.connection().execute(
        text("UPDATE training_versions SET content = :c, status = 'published' WHERE version_id = 1"),
        {"c": json.dumps(big4_content())},
    )


@pytest.fixture
def vocab(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str]]]:
    made: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(publish, "ensure_vocabulary", lambda _s, name, phrases: made.append((name, phrases)))
    return made


def preview_on(db: Session, version_id: int, *, spoke: bool = True, minutes_ago: int = 0) -> str:
    sid = f"pv-{version_id}-{minutes_ago}-{int(spoke)}"
    started = service._now() - timedelta(minutes=minutes_ago)
    insert(db, "training_sessions", session_id=sid, uid=900001, training_id="big4", version_id=version_id,
           room_name=f"pv-big4-900001-{sid[-6:]}", started_at=started, client="preview")  # fmt: skip
    insert(
        db, "session_transcripts", session_id=sid, seq=1, role="trainer", message="Hi!", created_at=started
    )
    if spoke:
        insert(db, "session_transcripts", session_id=sid, seq=2, role="trainee", message="Ready.",
               created_at=started)  # fmt: skip
    return sid


def run_worker(db: Session) -> dict[str, int]:
    return publish.run_publish_jobs(db, get_settings())


def ready(client: TestClient, headers: Headers, version_id: int) -> dict[str, Any]:
    r = client.get(f"/api/v1/versions/{version_id}/readiness", headers=headers)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def submitted_draft(client: TestClient, headers: Headers) -> int:
    vid = int(new_draft(client, headers)["version_id"])
    r = client.post(f"/api/v1/versions/{vid}/submit", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_review"
    return vid


def go(client: TestClient, headers: Headers, version_id: int, **body: Any) -> Any:
    return client.post(f"/api/v1/versions/{version_id}/publish", headers=headers, json=body)


def state(db: Session) -> dict[str, Any]:
    active = db.execute(text("SELECT active_version_id FROM trainings WHERE training_id = 'big4'")).scalar()
    statuses = dict(
        db.execute(text("SELECT version_id, status FROM training_versions WHERE training_id = 'big4'")).all()
    )
    return {"active": active, "statuses": statuses}


# --- Submit and send back --------------------------------------------------------------------------------


def test_submit_locks_the_content(client: TestClient, trainer_headers: Headers, studio: None) -> None:
    vid = submitted_draft(client, trainer_headers)
    content = client.get(f"/api/v1/versions/{vid}/content", headers=trainer_headers).json()
    save = client.put(f"/api/v1/versions/{vid}/content", headers=trainer_headers,
                      json={"revision": content["revision"], "content": content["content"]})  # fmt: skip
    assert (save.status_code, save.json()["error"]["code"]) == (409, "not_a_draft")
    again = client.post(f"/api/v1/versions/{vid}/submit", headers=trainer_headers)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "not_a_draft")


def test_a_draft_with_errors_cant_be_submitted(
    client: TestClient, trainer_headers: Headers, studio: None
) -> None:
    blank = client.post("/api/v1/trainings/big4/versions", headers=trainer_headers, json={"source": "blank"})
    r = client.post(f"/api/v1/versions/{blank.json()['version_id']}/submit", headers=trainer_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "has_errors")
    assert ready(client, trainer_headers, blank.json()["version_id"])["can_submit"] is False


def test_send_back_with_a_note(client: TestClient, trainer_headers: Headers, studio: None) -> None:
    vid = submitted_draft(client, trainer_headers)
    r = client.post(f"/api/v1/versions/{vid}/send-back", headers=trainer_headers,
                    json={"note": "Topic 4 is too long."})  # fmt: skip
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["notes"]) == ("draft", "Topic 4 is too long.")
    again = client.post(f"/api/v1/versions/{vid}/send-back", headers=trainer_headers, json={})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "not_in_review")
    # Submitting again clears the note.
    assert client.post(f"/api/v1/versions/{vid}/submit", headers=trainer_headers).json()["notes"] is None


# --- Readiness and the preview rule ------------------------------------------------------------------------


def test_publishing_needs_a_preview_after_the_last_edit(
    client: TestClient, trainer_headers: Headers, studio: None, db_session: Session
) -> None:
    vid = submitted_draft(client, trainer_headers)
    body = ready(client, trainer_headers, vid)
    assert (body["status"], body["can_publish"], body["preview"], body["needs_preview"]) == (
        "in_review", False, None, True
    )  # fmt: skip
    r = go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "not_ready")

    preview_on(db_session, vid, minutes_ago=60)  # before the draft existed: doesn't count
    preview_on(db_session, vid, spoke=False)  # nobody said anything: doesn't count
    preview_on(db_session, 1)  # another version: doesn't count
    assert ready(client, trainer_headers, vid)["preview"] is None

    sid = preview_on(db_session, vid)
    body = ready(client, trainer_headers, vid)
    assert body["preview"]["session_id"] == sid
    assert body["can_publish"] is True and body["blockers"] == []


def test_a_draft_cant_be_published(client: TestClient, trainer_headers: Headers, studio: None) -> None:
    vid = int(new_draft(client, trainer_headers)["version_id"])
    r = go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_publishable")


def test_no_completion_key_needs_confirming(
    client: TestClient, trainer_headers: Headers, studio: None, db_session: Session
) -> None:
    vid = submitted_draft(client, trainer_headers)
    preview_on(db_session, vid)
    r = go(client, trainer_headers, vid)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "no_completion_key")
    db_session.connection().execute(
        text("UPDATE trainings SET completion_key = 'ck-1' WHERE training_id = 'big4'")
    )
    assert go(client, trainer_headers, vid).status_code == 202


# --- The publish job ------------------------------------------------------------------------------------


def test_publish(
    client: TestClient,
    trainer_headers: Headers,
    studio: None,
    db_session: Session,
    vocab: list[tuple[str, list[str]]],
) -> None:
    db_session.execute(text(
        "UPDATE training_progress SET version_id = 1, first_started_at = UTC_TIMESTAMP(), passed_at = NULL "
        "WHERE uid = 1001 AND training_id = 'big4'"
    ))  # fmt: skip
    vid = submitted_draft(client, trainer_headers)
    preview_on(db_session, vid)
    r = go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert r.status_code == 202, r.text
    assert (r.json()["status"], r.json()["version_id"]) == ("queued", vid)
    twice = go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "publishing")
    assert state(db_session)["active"] == 1  # nothing changes until the job runs

    assert run_worker(db_session) == {"done": 1, "failed": 0}
    assert state(db_session) == {"active": vid, "statuses": {1: "retired", vid: "published"}}
    job = client.get(f"/api/v1/versions/{vid}/publish", headers=trainer_headers).json()
    name = f"nudgelab-big4-v{vid}"
    content = big4_content()
    assert job["status"] == "done"
    assert job["result"] == {"topics": len(content["knowledge_base"]["topics"]), "questions": 5,
                             "vocabulary": name, "retired": [1]}  # fmt: skip
    assert vocab == [(name, content["vocabulary"])]

    row = db_session.execute(text(
        "SELECT published_by, published_at, content FROM training_versions WHERE version_id = :v"
    ), {"v": vid}).one()  # fmt: skip
    assert row.published_by is not None and row.published_at is not None
    assert json.loads(row.content)["training"]["stt_vocabulary"] == name
    topics = db_session.execute(
        text("SELECT COUNT(*) FROM training_topics WHERE version_id = :v"), {"v": vid}
    )
    assert topics.scalar() == len(content["knowledge_base"]["topics"])
    first = db_session.execute(
        text(
            "SELECT section_code, options FROM training_questions"
            " WHERE version_id = :v AND question_number = 1"
        ),
        {"v": vid},
    ).one()
    assert first.section_code and json.loads(first.options)

    # The in-progress trainee is untouched: still pinned to v1 (the agent keeps them there until they pass).
    pinned = db_session.execute(text("SELECT version_id FROM training_progress WHERE uid = 1001")).scalar()
    assert pinned == 1
    audit = db_session.execute(
        text("SELECT COUNT(*) FROM dash_audit_log WHERE action = 'training_published'")
    )
    assert audit.scalar() == 1


def test_rollback(
    client: TestClient,
    trainer_headers: Headers,
    studio: None,
    db_session: Session,
    vocab: list[tuple[str, list[str]]],
) -> None:
    vid = submitted_draft(client, trainer_headers)
    preview_on(db_session, vid)
    go(client, trainer_headers, vid, no_completion_key_ok=True)
    run_worker(db_session)
    # v1 is retired now; publishing it again needs no new preview, and its topic rows aren't duplicated.
    body = ready(client, trainer_headers, 1)
    assert (body["status"], body["needs_preview"], body["can_publish"]) == ("retired", False, True)
    assert go(client, trainer_headers, 1, no_completion_key_ok=True).status_code == 202
    assert run_worker(db_session) == {"done": 1, "failed": 0}
    assert state(db_session) == {"active": 1, "statuses": {1: "published", vid: "retired"}}
    topics = db_session.execute(text("SELECT COUNT(*) FROM training_topics WHERE version_id = 1")).scalar()
    assert topics == 3  # seed()'s rows, as they were
    assert vocab[-1][0] == "nudgelab-big4-v1"


def test_a_failed_vocabulary_switches_nothing(
    client: TestClient,
    trainer_headers: Headers,
    studio: None,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: Any) -> None:
        raise publish.PublishError("Amazon Transcribe refused the vocabulary: bad phrase")

    monkeypatch.setattr(publish, "ensure_vocabulary", refuse)
    vid = submitted_draft(client, trainer_headers)
    preview_on(db_session, vid)
    go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert run_worker(db_session) == {"done": 0, "failed": 1}
    assert state(db_session) == {"active": 1, "statuses": {1: "published", vid: "in_review"}}
    job = client.get(f"/api/v1/versions/{vid}/publish", headers=trainer_headers).json()
    assert job["status"] == "failed" and "refused the vocabulary" in job["error"]
    # Fixed and retried: it goes through.
    monkeypatch.setattr(publish, "ensure_vocabulary", lambda *_: None)
    go(client, trainer_headers, vid, no_completion_key_ok=True)
    assert run_worker(db_session) == {"done": 1, "failed": 0}


def test_sent_back_while_queued_isnt_published(
    client: TestClient,
    trainer_headers: Headers,
    studio: None,
    db_session: Session,
    vocab: list[tuple[str, list[str]]],
) -> None:
    vid = submitted_draft(client, trainer_headers)
    preview_on(db_session, vid)
    go(client, trainer_headers, vid, no_completion_key_ok=True)
    r = client.post(f"/api/v1/versions/{vid}/send-back", headers=trainer_headers, json={})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "publishing")
    db_session.connection().execute(text("UPDATE training_versions SET status = 'draft' WHERE version_id=:v"),
                                    {"v": vid})  # fmt: skip
    assert run_worker(db_session) == {"done": 0, "failed": 1}
    assert state(db_session)["active"] == 1


# --- Transcribe -----------------------------------------------------------------------------------------


class FakeTranscribe:
    def __init__(self, states: list[str], exists: bool = False) -> None:
        self.states = states
        self.exists = exists
        self.created: list[dict[str, Any]] = []

    def get_vocabulary(self, VocabularyName: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        from botocore.exceptions import ClientError

        if not self.exists:
            raise ClientError(
                {"Error": {"Code": "BadRequestException", "Message": "not found"}}, "GetVocabulary"
            )
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"VocabularyState": state, "FailureReason": "phrase 'x y' isn't valid"}

    def create_vocabulary(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        self.exists = True
        return {}


@pytest.mark.parametrize(
    ("exists", "states", "created"), [(False, ["PENDING", "READY"], 1), (True, ["READY"], 0)]
)
def test_ensure_vocabulary(
    monkeypatch: pytest.MonkeyPatch, exists: bool, states: list[str], created: int
) -> None:
    fake = FakeTranscribe(states, exists)
    monkeypatch.setattr(publish.boto3, "client", lambda *_a, **_k: fake)
    monkeypatch.setattr(publish, "VOCABULARY_POLL_SECONDS", 0)
    publish.ensure_vocabulary(get_settings(), "nudgelab-big4-v9", ["P.P.V.G.A."])
    assert len(fake.created) == created
    if created:
        assert fake.created[0] == {"VocabularyName": "nudgelab-big4-v9", "LanguageCode": "en-US",
                                   "Phrases": ["P.P.V.G.A."]}  # fmt: skip


def test_ensure_vocabulary_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish.boto3, "client", lambda *_a, **_k: FakeTranscribe(["FAILED"], exists=True))
    with pytest.raises(publish.PublishError, match="refused the vocabulary"):
        publish.ensure_vocabulary(get_settings(), "nudgelab-big4-v9", ["x"])


def test_vocabulary_names() -> None:
    assert publish.vocabulary_name("q4_comp_2026", 41) == "nudgelab-q4-comp-2026-v41"
