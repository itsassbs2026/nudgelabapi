"""Saved views and the team list (Phase 8)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def save(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post("/api/v1/saved-views", headers=headers, json={"page": "quality", **body})


def test_save_list_delete(client: TestClient, trainer_headers: dict[str, str]) -> None:
    created = save(client, trainer_headers, name="  Open tone issues ", query="?status=open&issue_type=tone")
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["name"] == "Open tone issues" and view["query"] == "status=open&issue_type=tone"
    listed = client.get("/api/v1/saved-views", headers=trainer_headers, params={"page": "quality"}).json()
    assert listed == [view]
    assert (
        client.get("/api/v1/saved-views", headers=trainer_headers, params={"page": "sessions"}).json() == []
    )
    assert client.delete(f"/api/v1/saved-views/{view['id']}", headers=trainer_headers).status_code == 204
    assert client.get("/api/v1/saved-views", headers=trainer_headers, params={"page": "quality"}).json() == []


def test_views_are_private(
    client: TestClient, trainer_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    view = save(client, trainer_headers, name="Mine", query="status=open").json()
    assert client.get("/api/v1/saved-views", headers=admin_headers, params={"page": "quality"}).json() == []
    assert client.delete(f"/api/v1/saved-views/{view['id']}", headers=admin_headers).status_code == 404


def test_bad_views_are_refused(client: TestClient, trainer_headers: dict[str, str]) -> None:
    assert save(client, trainer_headers, name="x", query="a=<script>").status_code == 422
    assert save(client, trainer_headers, name="   ", query="a=1").status_code == 422
    assert save(client, trainer_headers, name="x", query="a=1", page="admin").status_code == 422


def test_team_lists_active_users(client: TestClient, trainer_headers: dict[str, str], make_user: Any) -> None:
    gone = make_user(email="gone@example.com", is_active=False)
    names = {m["full_name"] for m in client.get("/api/v1/team", headers=trainer_headers).json()}
    assert gone.full_name not in names and len(names) >= 1
    member = client.get("/api/v1/team", headers=trainer_headers).json()[0]
    assert set(member) == {"id", "full_name", "role"}  # no emails or account details
