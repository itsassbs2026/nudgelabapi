"""The Admin's "Sync user/store list from Portal": emailed code, checks, one job at a time, the worker run."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from app.config import get_settings
from app.reference import manual
from app.reference.sync import TableResult
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

Headers = dict[str, str]
URL = "/api/v1/admin/reference-sync"


@pytest.fixture
def sync_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "portallive_database_url", "mysql+pymysql://ro:x@127.0.0.1:1/primetwok")
    monkeypatch.setattr(settings, "reference_sync_database_url", "mysql+pymysql://sync:x@127.0.0.1:1/nudgeai")


def emailed_code(db: Session) -> str:
    subject = db.execute(text("SELECT subject FROM dash_email_outbox ORDER BY id DESC LIMIT 1")).scalar_one()
    match = re.search(r"\b(\d{6})\b", subject)
    assert match, subject
    return match.group(1)


def actions(db: Session) -> list[str]:
    sql = "SELECT action FROM dash_audit_log WHERE action LIKE 'reference_sync%' ORDER BY id"
    return list(db.execute(text(sql)).scalars())


def test_admins_only(client: TestClient, trainer_headers: Headers, sync_on: None) -> None:
    assert client.get(URL, headers=trainer_headers).status_code == 403
    assert client.post(f"{URL}/code", headers=trainer_headers).status_code == 403
    assert client.post(URL, headers=trainer_headers, json={"code": "123456"}).status_code == 403


def test_not_set_up(client: TestClient, admin_headers: Headers) -> None:
    r = client.post(f"{URL}/code", headers=admin_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "sync_not_configured")
    assert client.get(URL, headers=admin_headers).json()["configured"] is False


def test_code_then_sync(
    client: TestClient, admin_headers: Headers, sync_on: None, db_session: Session
) -> None:
    r = client.post(f"{URL}/code", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == 600 and re.fullmatch(r"\w\*{3,}@[\w.]+", r.json()["sent_to"])
    code = emailed_code(db_session)
    stored = db_session.execute(text("SELECT code_hash FROM dash_action_codes")).scalar_one()
    assert code not in stored  # never stored in clear

    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    r = client.post(URL, headers=admin_headers, json={"code": wrong})
    assert (r.status_code, r.json()["error"]["code"], r.json()["error"]["details"]) == (
        400, "code_wrong", {"attempts_left": 4}
    )  # fmt: skip
    r = client.post(URL, headers=admin_headers, json={"code": f" {code} "})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    again = client.post(URL, headers=admin_headers, json={"code": code})
    assert again.json()["error"]["code"] == "code_expired"  # works once
    status = client.get(URL, headers=admin_headers).json()
    assert status["manual"]["id"] == job_id and status["manual"]["status"] == "queued"
    assert [t["table"] for t in status["tables"]] == ["v_users_all", "v_users", "v_stores", "v_stores_all"]
    assert actions(db_session) == [
        "reference_sync_code_sent",
        "reference_sync_code_failed",
        "reference_sync_started",
    ]


def test_five_wrong_codes_lock_it(
    client: TestClient, admin_headers: Headers, sync_on: None, db_session: Session
) -> None:
    client.post(f"{URL}/code", headers=admin_headers)
    code = emailed_code(db_session)
    wrong = f"{(int(code) + 7) % 1_000_000:06d}"
    for _ in range(4):
        assert (
            client.post(URL, headers=admin_headers, json={"code": wrong}).json()["error"]["code"]
            == "code_wrong"
        )
    assert (
        client.post(URL, headers=admin_headers, json={"code": wrong}).json()["error"]["code"] == "code_locked"
    )
    assert (
        client.post(URL, headers=admin_headers, json={"code": code}).json()["error"]["code"] == "code_expired"
    )


def test_codes_expire_and_a_new_one_replaces_the_old(
    client: TestClient, admin_headers: Headers, sync_on: None, db_session: Session
) -> None:
    client.post(f"{URL}/code", headers=admin_headers)
    first = emailed_code(db_session)
    client.post(f"{URL}/code", headers=admin_headers)
    second = emailed_code(db_session)
    if first != second:
        assert (
            client.post(URL, headers=admin_headers, json={"code": first}).json()["error"]["code"]
            == "code_wrong"
        )
    db_session.execute(text("UPDATE dash_action_codes SET expires_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE"))
    assert (
        client.post(URL, headers=admin_headers, json={"code": second}).json()["error"]["code"]
        == "code_expired"
    )


def test_three_codes_a_quarter_hour(client: TestClient, admin_headers: Headers, sync_on: None) -> None:
    for _ in range(3):
        assert client.post(f"{URL}/code", headers=admin_headers).status_code == 200
    r = client.post(f"{URL}/code", headers=admin_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (429, "too_many_codes")


def test_one_sync_at_a_time(
    client: TestClient, admin_headers: Headers, sync_on: None, db_session: Session
) -> None:
    db_session.execute(
        text("INSERT INTO jobs (type, status, input, attempts) VALUES ('reference_sync', 'running', '{}', 1)")
    )
    client.post(f"{URL}/code", headers=admin_headers)
    r = client.post(URL, headers=admin_headers, json={"code": emailed_code(db_session)})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "sync_running")


@pytest.mark.parametrize("fail_one", [False, True])
def test_the_worker_runs_the_sync(
    client: TestClient, admin_headers: Headers, sync_on: None, db_session: Session,
    monkeypatch: pytest.MonkeyPatch, fail_one: bool,
) -> None:  # fmt: skip
    client.post(f"{URL}/code", headers=admin_headers)
    client.post(URL, headers=admin_headers, json={"code": emailed_code(db_session)})

    def fake_sync(target: Any, fetch: Any) -> list[TableResult]:
        out = [
            TableResult(t, "success", 10, 11) for t in ("v_users_all", "v_users", "v_stores", "v_stores_all")
        ]
        if fail_one:
            out[1] = TableResult("v_users", "failed", 10, 0, "v_users: PortalLive returned 0 rows")
        return out

    monkeypatch.setattr(manual, "sync_reference_tables", fake_sync)
    assert manual.run_reference_sync_jobs(db_session, get_settings()) == {
        "done": int(not fail_one),
        "failed": int(fail_one),
    }
    manual_job = client.get(URL, headers=admin_headers).json()["manual"]
    assert manual_job["status"] == ("failed" if fail_one else "done")
    assert [t["status"] for t in manual_job["tables"]].count("success") == (3 if fail_one else 4)
    assert ("v_users" in (manual_job["error"] or "")) is fail_one
    assert manual.run_reference_sync_jobs(db_session, get_settings()) == {
        "done": 0,
        "failed": 0,
    }  # nothing queued


def test_mask_email() -> None:
    assert manual.mask_email("bgupta@primecomms.com") == "b*****@primecomms.com"
    assert manual.mask_email("al@x.com") == "a***@x.com"
    assert timedelta(minutes=manual.CODE_MINUTES) == timedelta(minutes=10)


def test_next_scheduled_run() -> None:
    # 2026-10-07 is CDT (UTC-5): 8:00 AM = 13:00 UTC, 11:00 PM = 04:00 UTC the next day.
    from datetime import datetime

    assert manual.next_scheduled(datetime(2026, 10, 7, 12, 0)) == datetime(2026, 10, 7, 13, 0)
    assert manual.next_scheduled(datetime(2026, 10, 7, 13, 0)) == datetime(2026, 10, 8, 4, 0)
    assert manual.next_scheduled(datetime(2026, 10, 8, 4, 30)) == datetime(2026, 10, 8, 13, 0)


def test_the_last_automatic_run_isnt_the_manual_one(
    client: TestClient, admin_headers: Headers, db_session: Session
) -> None:
    """2026-10-07: the page showed only the 12:04 AM button press, so the 8:00 AM timer run looked missing."""
    from datetime import datetime

    from app.models.dashboard import Job
    from app.models.sync_run_log import SyncRunLog

    def run(at: datetime, status: str = "success") -> None:
        for table in ("v_users_all", "v_users", "v_stores", "v_stores_all"):
            end = at + timedelta(seconds=8)
            db_session.add(SyncRunLog(sync_name="portallive_reference", table_name=table, started_at=at,
                                      finished_at=end, status=status, row_count=10))  # fmt: skip

    assert client.get(URL, headers=admin_headers).json()["last_automatic"] is None
    run(datetime(2026, 10, 7, 13, 0, 1))  # the timer, 8:00 AM CDT
    manual_at = datetime(2026, 10, 7, 14, 30, 0)
    db_session.add(Job(type="reference_sync", status="done", input={}, attempts=1, started_at=manual_at,
                       finished_at=manual_at + timedelta(seconds=20)))  # fmt: skip
    run(manual_at + timedelta(seconds=2))  # the button, later
    db_session.flush()
    body = client.get(URL, headers=admin_headers).json()
    assert body["last_automatic"] == {"at": "2026-10-07T13:00:09", "status": "success"}
    assert body["next_automatic_at"]
    assert body["tables"][0]["last_run_at"] == "2026-10-07T14:30:10"  # the table shows the latest of either
