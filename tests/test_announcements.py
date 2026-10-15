"""What's new (2026-10-15): each person's seen announcements, kept once, private to them."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

URL = "/api/v1/me/announcements"


def test_an_announcement_is_recorded_once_per_person(
    client: TestClient, trainer_headers: dict[str, str], admin_headers: dict[str, str], db_session: Session
) -> None:
    assert client.get(URL, headers=trainer_headers).json() == []
    assert client.put(f"{URL}/ask-nudgelab", headers=trainer_headers).status_code == 204
    assert client.put(f"{URL}/ask-nudgelab", headers=trainer_headers).status_code == 204  # again: one row
    assert client.put(f"{URL}/whats-new-2", headers=trainer_headers).status_code == 204
    assert sorted(client.get(URL, headers=trainer_headers).json()) == ["ask-nudgelab", "whats-new-2"]
    assert client.get(URL, headers=admin_headers).json() == []  # someone else's are theirs
    rows = db_session.execute(text("SELECT COUNT(*) FROM dash_announcements_seen")).scalar_one()
    assert rows == 2


def test_announcement_keys_are_plain(client: TestClient, trainer_headers: dict[str, str]) -> None:
    assert client.put(f"{URL}/Ask%20NudgeLab", headers=trainer_headers).status_code == 422
    assert client.put(f"{URL}/{'x' * 65}", headers=trainer_headers).status_code == 422
    assert client.get(URL).status_code == 401
