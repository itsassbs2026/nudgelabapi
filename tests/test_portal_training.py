"""Training in the employee portal (2026-10-29): the portal signs its own pass, asks for a one-time Start link
and the training page redeems it, once, for the voice session (app/mobile/handoff.py)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import jwt as pyjwt
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.test_app_api import (  # noqa: F401 - fixtures
    ME,
    OTHER,
    app_data,
    bearer,
    livekit,
    make_pass,
    wanaka_key,
)


def portal_pass(make_pass: Callable[..., str], uid: int = ME) -> str:
    return make_pass(uid, iss="portal")


def handoff(client: TestClient, token: str, **body: object) -> object:
    return client.post("/app/v1/handoffs", headers=bearer(token), json={"training_id": "big4", **body})


def test_the_portal_signs_its_own_pass(
    client: TestClient, app_data: None, make_pass: Callable[..., str]
) -> None:
    listed = client.get("/app/v1/trainings", headers=bearer(portal_pass(make_pass)))
    assert listed.status_code == 200, listed.text
    assert client.get("/app/v1/trainings", headers=bearer(make_pass(iss="someone-else"))).status_code == 401


def test_a_start_link_is_one_time_and_only_its_hash_is_kept(
    client: TestClient, app_data: None, make_pass: Callable[..., str], db_session: Session
) -> None:
    r = handoff(client, portal_pass(make_pass), return_url="https://alpha.myprimeportal.com/my-training")
    assert r.status_code == 201, r.text
    body = r.json()
    url = urlsplit(body["url"])
    assert f"{url.scheme}://{url.netloc}" == "https://training.myprimeportal.com"
    assert parse_qs(url.query)["h"] == [body["code"]] and 60 <= body["expires_in"] <= 120
    row = db_session.execute(text("SELECT code_hash, uid, training_id, return_url FROM app_handoffs")).one()
    assert row.code_hash == hashlib.sha256(body["code"].encode()).hexdigest() and body["code"] not in row
    assert (row.uid, row.training_id, row.return_url) == (
        ME,
        "big4",
        "https://alpha.myprimeportal.com/my-training",
    )


def test_a_return_address_off_the_portal_is_dropped(
    client: TestClient, app_data: None, make_pass: Callable[..., str], db_session: Session
) -> None:
    for bad in (
        "https://evil.example/x",
        "http://myprimeportal.com/x",
        "https://myprimeportal.com.evil.example/",
    ):
        assert handoff(client, portal_pass(make_pass), return_url=bad).status_code == 201
    kept = db_session.execute(text("SELECT return_url FROM app_handoffs")).scalars().all()
    assert kept == [None, None, None]


def test_only_for_a_signed_in_employee_and_their_own_training(
    client: TestClient, app_data: None, make_pass: Callable[..., str]
) -> None:
    assert client.post("/app/v1/handoffs", json={"training_id": "big4"}).status_code == 401
    assert handoff(client, portal_pass(make_pass, OTHER)).status_code == 403  # big4 isn't assigned to them
    assert handoff(client, portal_pass(make_pass), training_id="nope").status_code == 404


def test_the_training_page_redeems_it_once_for_a_portal_session(
    client: TestClient,
    app_data: None,
    make_pass: Callable[..., str],
    livekit: str,
    db_session: Session,
) -> None:
    code = handoff(
        client, portal_pass(make_pass), start_over=True, return_url="https://myprimeportal.com/my-training"
    ).json()["code"]
    r = client.post("/app/v1/handoffs/redeem", json={"code": code})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["training_id"] == "big4" and body["training_title"] == "RSC Sales Incentive Plan"
    assert body["return_url"] == "https://myprimeportal.com/my-training" and body["first_name"] == "Akbar"
    assert body["room_name"].startswith(f"nl-big4-{ME}-")
    claims = pyjwt.decode(
        body["participant_token"], livekit, algorithms=["HS256"], options={"verify_aud": False}
    )
    assert claims["video"]["canUpdateOwnMetadata"] is True  # the page says it shows images
    meta = json.loads(claims["roomConfig"]["agents"][0]["metadata"])
    assert (meta["uid"], meta["client"], meta["reset"]) == (
        ME,
        "portal",
        True,
    )  # reports can tell the portal apart

    again = client.post("/app/v1/handoffs/redeem", json={"code": code})
    assert again.status_code == 410 and again.json()["error"]["code"] == "handoff_expired"


def test_an_old_or_unknown_link_is_refused(
    client: TestClient, app_data: None, make_pass: Callable[..., str], livekit: str, db_session: Session
) -> None:
    code = handoff(client, portal_pass(make_pass)).json()["code"]
    db_session.execute(text("UPDATE app_handoffs SET expires_at = utc_timestamp(6) - INTERVAL 1 SECOND"))
    assert client.post("/app/v1/handoffs/redeem", json={"code": code}).status_code == 410
    assert client.post("/app/v1/handoffs/redeem", json={"code": "x" * 32}).status_code == 410
    assert client.post("/app/v1/handoffs/redeem", json={"code": "short"}).status_code == 422
