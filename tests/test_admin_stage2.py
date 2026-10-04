"""Voices, setups and testers (SPEC 10.3, Phase 14). Polly's voice list is replaced by a fake.

Acceptance (SPEC §15): testers.json is retired; testers imported from it keep their access codes.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest
from app.studio import admin
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import insert, seed

API = "/api/v1/admin"
REAL_POLLY_VOICES = admin.polly_voices  # before the autouse fake replaces it
HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "us.anthropic.claude-sonnet-5-5"
POLLY = [
    {"voice_id": "Ruth", "name": "Ruth", "gender": "Female", "language_code": "en-US", "language_name": "US"},
    {
        "voice_id": "Joanna",
        "name": "Joanna",
        "gender": "Female",
        "language_code": "en-US",
        "language_name": "US",
    },
    {
        "voice_id": "Arthur",
        "name": "Arthur",
        "gender": "Male",
        "language_code": "en-GB",
        "language_name": "GB",
    },
]


@pytest.fixture
def data(db_session: Session) -> None:
    """seed(): voice Matthew, setup `standard`, trainings big4 and walk (both runnable). Plus Ruth (the
    default voice), Amy (switched off), and a second setup `deep` (the default)."""
    seed(db_session)
    insert(db_session, "training_voices", voice_id="Ruth", display_name="Ruth", language_code="en-US",
           gender="Female", is_default=1, sort_order=10)  # fmt: skip
    insert(db_session, "training_voices", voice_id="Amy", display_name="Amy", language_code="en-GB",
           gender="Female", is_active=0, sort_order=80)  # fmt: skip
    insert(db_session, "training_profiles", profile_id="deep", display_name="Deep", llm_model=SONNET,
           llm_effort="low", llm_input_per_m=3, llm_cached_per_m=0.3, llm_cache_write_per_m=3.75,
           llm_output_per_m=15, tts_per_m_chars=30, stt_per_minute=0.024, is_default=1)  # fmt: skip
    insert(db_session, "trainings", training_id="draft_only", title="Draft", status="draft",
           completion_type="quiz")  # fmt: skip


@pytest.fixture(autouse=True)
def polly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "polly_voices", lambda _settings: [dict(v) for v in POLLY])


def audit_actions(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(text("SELECT action, target_type FROM dash_audit_log ORDER BY id")).all()
    return [(r.action, r.target_type) for r in rows if r.action in ("settings_changed", "tester_changed")]


def err(r: Any) -> str:
    return str(r.json()["error"]["code"])


# --- Voices ----------------------------------------------------------------------------------------------


def defaults(rows: list[dict[str, Any]]) -> list[str]:
    return [v["voice_id"] for v in rows if v["is_default"]]


def test_voice_switch_and_notes(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    r = client.patch(f"{API}/voices/Matthew", headers=admin_headers,
                     json={"is_active": False, "notes": "Too fast", "sort_order": 5})  # fmt: skip
    assert r.status_code == 200, r.text
    matthew = next(v for v in r.json() if v["voice_id"] == "Matthew")
    assert (matthew["is_active"], matthew["notes"], matthew["sort_order"]) == (False, "Too fast", 5)


def test_default_voice_cant_be_switched_off(
    client: TestClient, admin_headers: dict[str, str], data: None
) -> None:
    r = client.patch(f"{API}/voices/Ruth", headers=admin_headers, json={"is_active": False})
    assert (r.status_code, err(r)) == (409, "default_voice")


@pytest.mark.parametrize("voice", ["Amy", "Matthew"])  # sorts before and after the old default, Ruth
def test_make_default_voice(
    client: TestClient, admin_headers: dict[str, str], data: None, voice: str
) -> None:
    client.patch(f"{API}/voices/Amy", headers=admin_headers, json={"is_active": True})
    r = client.post(f"{API}/voices/{voice}/default", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert defaults(r.json()) == [voice]


def test_voice_must_be_on_to_be_default(
    client: TestClient, admin_headers: dict[str, str], data: None
) -> None:
    r = client.post(f"{API}/voices/Amy/default", headers=admin_headers)
    assert (r.status_code, err(r)) == (409, "voice_off")
    assert client.post(f"{API}/voices/Nobody/default", headers=admin_headers).status_code == 404


def test_available_and_add_voice(
    client: TestClient, admin_headers: dict[str, str], data: None, db_session: Session
) -> None:
    available = client.get(f"{API}/voices/available", headers=admin_headers).json()
    assert [v["voice_id"] for v in available] == ["Arthur", "Joanna"]  # Ruth is listed already
    r = client.post(f"{API}/voices", headers=admin_headers, json={"voice_id": "Arthur"})
    assert r.status_code == 200, r.text
    arthur = next(v for v in r.json() if v["voice_id"] == "Arthur")
    assert (arthur["gender"], arthur["language_code"], arthur["is_active"], arthur["is_default"]) == (
        "Male", "en-GB", True, False
    )  # fmt: skip
    assert arthur["sort_order"] == 90  # after Amy's 80
    again = client.post(f"{API}/voices", headers=admin_headers, json={"voice_id": "Arthur"})
    assert (again.status_code, err(again)) == (409, "voice_exists")
    unknown = client.post(f"{API}/voices", headers=admin_headers, json={"voice_id": "Brian"})
    assert (unknown.status_code, err(unknown)) == (422, "unknown_voice")
    assert ("settings_changed", "voice") in audit_actions(db_session)


def test_polly_voices_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    pages = [
        {"Voices": [{"Id": "Ruth", "Name": "Ruth", "Gender": "Female", "LanguageCode": "en-US"}],
         "NextToken": "t1"},
        {"Voices": [{"Id": "Lea", "Gender": "Female", "LanguageCode": "fr-FR"},
                    {"Id": "Olivia", "Gender": "Female", "LanguageCode": "en-AU", "LanguageName": "AU"}]},
    ]  # fmt: skip
    seen: list[dict[str, Any]] = []

    class Polly:
        def describe_voices(self, **kwargs: Any) -> Any:
            seen.append(kwargs)
            return pages[len(seen) - 1]

    monkeypatch.setattr(admin.boto3, "client", lambda *_a, **_k: Polly())
    assert [v["voice_id"] for v in REAL_POLLY_VOICES(get_settings())] == ["Ruth", "Olivia"]  # English only
    assert seen == [{"Engine": "generative"}, {"Engine": "generative", "NextToken": "t1"}]


# --- Setups ----------------------------------------------------------------------------------------------


def profile(body: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in body["profiles"] if p["profile_id"] == pid)


def test_profiles_list(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    body = client.get(f"{API}/profiles", headers=admin_headers).json()
    assert body["models"] == [
        {"llm_model": HAIKU, "label": "Claude Haiku 4.5", "effort": False},
        {"llm_model": SONNET, "label": "Claude Sonnet 5.5", "effort": True},
    ]
    deep, standard = body["profiles"]
    assert (deep["profile_id"], deep["llm_model_label"], deep["is_default"], deep["llm_input_per_m"]) == (
        "deep", "Claude Sonnet 5.5", True, 3.0
    )  # fmt: skip
    assert (standard["llm_model_known"], standard["llm_model_label"]) == (False, "haiku")


def test_profile_update(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    r = client.patch(f"{API}/profiles/standard", headers=admin_headers, json={
        "llm_model": HAIKU, "display_name": "Everyday", "llm_output_per_m": "5.5", "voice_id": "Matthew",
        "allow_request": True, "notes": None,
    })  # fmt: skip
    assert r.status_code == 200, r.text
    standard = profile(r.json(), "standard")
    assert (standard["display_name"], standard["llm_output_per_m"], standard["voice_id"]) == (
        "Everyday", 5.5, "Matthew"
    )  # fmt: skip
    assert (standard["llm_model_known"], standard["allow_request"]) == (True, True)


@pytest.mark.parametrize(
    ("pid", "body", "status", "code"),
    [
        ("standard", {"llm_model": "gpt-5"}, 422, "unknown_model"),
        ("standard", {"llm_model": HAIKU, "llm_effort": "high"}, 422, "no_effort"),
        ("deep", {"llm_model": HAIKU}, 422, "no_effort"),  # deep has effort "low"
        ("deep", {"voice_id": "Amy"}, 422, "voice_off"),
        ("deep", {"voice_id": "Nobody"}, 422, "voice_off"),
        ("deep", {"is_active": False}, 409, "default_setup"),
        ("deep", {"llm_input_per_m": -1}, 422, "validation_error"),
        ("deep", {"display_name": None}, 422, "validation_error"),
        ("deep", {"is_default": True}, 422, "validation_error"),  # via /default only
        ("nobody", {"notes": "x"}, 404, "not_found"),
    ],
)
def test_profile_update_refused(
    client: TestClient, admin_headers: dict[str, str], data: None, pid: str, body: dict[str, Any],
    status: int, code: str,
) -> None:  # fmt: skip
    r = client.patch(f"{API}/profiles/{pid}", headers=admin_headers, json=body)
    assert (r.status_code, err(r)) == (status, code)


def test_effort_off_with_haiku(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    r = client.patch(
        f"{API}/profiles/deep", headers=admin_headers, json={"llm_model": HAIKU, "llm_effort": None}
    )
    assert r.status_code == 200, r.text
    assert profile(r.json(), "deep")["llm_effort"] is None


def test_make_default_profile(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    r = client.post(f"{API}/profiles/standard/default", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert [p["profile_id"] for p in r.json()["profiles"] if p["is_default"]] == ["standard"]
    client.patch(f"{API}/profiles/deep", headers=admin_headers, json={"is_active": False})
    off = client.post(f"{API}/profiles/deep/default", headers=admin_headers)
    assert (off.status_code, err(off)) == (409, "setup_off")


# --- Testers ---------------------------------------------------------------------------------------------


def web_py_hash(code: str) -> str:
    """The agent's web.py `_hash`, copied: what a tester types, trimmed and uppercased, SHA-256."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def stored_hash(db: Session, uid: int) -> str:
    return str(db.execute(text("SELECT code_hash FROM testers WHERE uid = :u"), {"u": uid}).scalar())


def add(
    client: TestClient, headers: dict[str, str], uid: int = 3784, trainings: list[str] | None = None
) -> Any:
    body = {"uid": uid, "name": " Pat Tester ", "trainings": trainings or ["big4"]}
    return client.post(f"{API}/testers", headers=headers, json=body)


def test_create_tester_shows_code_once(
    client: TestClient, admin_headers: dict[str, str], data: None, db_session: Session
) -> None:
    r = add(client, admin_headers, trainings=["big4", "walk", "big4"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert re.fullmatch(r"[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}", body["code"])
    assert body["tester"]["name"] == "Pat Tester"
    assert body["tester"]["trainings"] == ["big4", "walk"]
    assert body["tester"]["created_by"].startswith("User ")
    # Only the hash is kept, and it's the hash web.py makes of what the tester types (any case, spaces).
    assert stored_hash(db_session, 3784) == web_py_hash(f"  {body['code'].lower()} ")
    listed = client.get(f"{API}/testers", headers=admin_headers).json()
    assert [t["uid"] for t in listed] == [3784]
    assert "code" not in listed[0] and "code_hash" not in listed[0]
    assert ("tester_changed", "tester") in audit_actions(db_session)


@pytest.mark.parametrize(
    ("trainings", "status", "code"),
    [
        (["nope"], 422, "unknown_training"),
        (["draft_only"], 422, "unknown_training"),  # no version the agent can run
        ([], 422, "validation_error"),
        (["Bad Id"], 422, "validation_error"),
    ],
)
def test_create_tester_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    data: None,
    trainings: list[str],
    status: int,
    code: str,
) -> None:
    r = client.post(f"{API}/testers", headers=admin_headers,
                    json={"uid": 3784, "name": "Pat", "trainings": trainings})  # fmt: skip
    assert (r.status_code, err(r)) == (status, code)


def test_one_tester_per_uid(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    add(client, admin_headers)
    r = add(client, admin_headers)
    assert (r.status_code, err(r)) == (409, "tester_exists")


def test_update_tester(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    tid = add(client, admin_headers).json()["tester"]["id"]
    r = client.patch(f"{API}/testers/{tid}", headers=admin_headers,
                     json={"trainings": ["walk"], "is_active": False, "name": "Pat T"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert (r.json()["trainings"], r.json()["is_active"], r.json()["name"]) == (["walk"], False, "Pat T")
    bad = client.patch(f"{API}/testers/{tid}", headers=admin_headers, json={"trainings": ["nope"]})
    assert (bad.status_code, err(bad)) == (422, "unknown_training")
    empty = client.patch(f"{API}/testers/{tid}", headers=admin_headers, json={})
    assert (empty.status_code, err(empty)) == (422, "no_changes")
    assert client.patch(f"{API}/testers/999999", headers=admin_headers, json={"name": "X"}).status_code == 404


def test_new_code_replaces_the_old(
    client: TestClient, admin_headers: dict[str, str], data: None, db_session: Session
) -> None:
    first = add(client, admin_headers).json()
    r = client.post(f"{API}/testers/{first['tester']['id']}/new-code", headers=admin_headers)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code != first["code"]
    assert stored_hash(db_session, 3784) == web_py_hash(code)
    assert r.json()["tester"]["code_changed_at"] is not None


def test_import_testers_json(
    client: TestClient, admin_headers: dict[str, str], data: None, db_session: Session
) -> None:
    add(client, admin_headers, uid=1001)
    testers_json = {"testers": [
        {"uid": 1001, "name": "Here", "trainings": ["big4"], "code_sha256": web_py_hash("AAAA-BBBB-CCCC")},
        {"uid": 3784, "name": "Bot", "trainings": ["big4", "gone", "walk"],
         "code_sha256": web_py_hash("DDDD-EEEE-FFFF")},
        {"uid": 4000, "name": "Sam", "trainings": ["walk"], "code_sha256": web_py_hash("GGGG-HHHH-JJJJ")},
    ]}  # fmt: skip
    r = client.post(f"{API}/testers/import", headers=admin_headers, json=testers_json)
    assert r.status_code == 200, r.text
    assert r.json() == {"added": [3784, 4000], "skipped": [1001], "dropped_trainings": {"3784": ["gone"]}}
    # Everyone keeps their code: the hash is stored as it was.
    assert stored_hash(db_session, 3784) == web_py_hash("dddd-eeee-ffff")
    bot = next(t for t in client.get(f"{API}/testers", headers=admin_headers).json() if t["uid"] == 3784)
    assert bot["trainings"] == ["big4", "walk"]


def test_import_refuses_shared_codes(
    client: TestClient, admin_headers: dict[str, str], data: None, db_session: Session
) -> None:
    same = web_py_hash("AAAA-BBBB-CCCC")
    r = client.post(f"{API}/testers/import", headers=admin_headers, json={"testers": [
        {"uid": 1, "name": "A", "trainings": ["big4"], "code_sha256": same},
        {"uid": 2, "name": "B", "trainings": ["big4"], "code_sha256": same},
    ]})  # fmt: skip
    assert (r.status_code, err(r)) == (409, "duplicate_code")
    assert db_session.execute(text("SELECT COUNT(*) FROM testers")).scalar() == 0  # nothing imported


def test_import_checks_hashes(client: TestClient, admin_headers: dict[str, str], data: None) -> None:
    r = client.post(f"{API}/testers/import", headers=admin_headers, json={"testers": [
        {"uid": 1, "name": "A", "trainings": ["big4"], "code_sha256": "AAAA-BBBB-CCCC"},
    ]})  # fmt: skip
    assert (r.status_code, err(r)) == (422, "validation_error")


def test_code_alphabet_matches_web_py() -> None:
    codes = {admin.new_code() for _ in range(200)}
    assert len(codes) == 200
    assert set("".join(codes).replace("-", "")) <= set(admin.CODE_ALPHABET)
    assert not set("01OIL") & set(admin.CODE_ALPHABET)
