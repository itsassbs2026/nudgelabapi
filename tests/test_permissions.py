"""Authorization matrix (SPEC §0.3, §12.1).

Every protected route is called as anonymous, Trainer and Admin. A new route must be added here, or
test_every_route_is_in_the_matrix fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.config import get_settings
from app.main import app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

PUBLIC = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
}

# (method, path, body, trainer allowed?)
PROTECTED: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("POST", "/api/v1/auth/logout", None, True),
    ("POST", "/api/v1/auth/change-password", {"current_password": "x", "new_password": "y"}, True),
    ("GET", "/api/v1/me", None, True),
    ("PATCH", "/api/v1/me", {"timezone": "UTC"}, True),
    ("GET", "/api/v1/admin/users", None, False),
    ("POST", "/api/v1/admin/users", {"email": "p@example.com", "full_name": "P"}, False),
    ("GET", "/api/v1/admin/users/{user_id}", None, False),
    ("PATCH", "/api/v1/admin/users/{user_id}", {"full_name": "P"}, False),
    ("POST", "/api/v1/admin/users/{user_id}/reset-password", {}, False),
    ("GET", "/api/v1/admin/audit", None, False),
    ("GET", "/api/v1/reports/overview", None, True),
    ("GET", "/api/v1/reports/trainings", None, True),
    ("GET", "/api/v1/reports/trainings/{training_key}", None, True),
    ("GET", "/api/v1/reports/trainings/{training_key}/questions", None, True),
    ("GET", "/api/v1/reports/drilldown", None, True),
    ("GET", "/api/v1/reports/cost", None, True),
    ("GET", "/api/v1/reports/filter-options", None, True),
    ("GET", "/api/v1/employees/{uid}", None, True),
    ("GET", "/api/v1/feedback", None, True),
    ("GET", "/api/v1/acknowledgments", None, True),
    ("GET", "/api/v1/roleplay", None, True),
    ("GET", "/api/v1/assignments", None, True),
    ("GET", "/api/v1/sessions", None, True),
    ("GET", "/api/v1/sessions/{session_id}", None, True),
    ("POST", "/api/v1/sessions/{session_id}/recording-url", None, True),
    ("GET", "/api/v1/search?q=ab", None, True),
    ("GET", "/api/v1/live", None, True),
    ("GET", "/api/v1/saved-views?page=quality", None, True),
    ("POST", "/api/v1/saved-views", {"page": "quality", "name": "Mine", "query": "status=open"}, True),
    ("DELETE", "/api/v1/saved-views/{view_id}", None, True),
    ("GET", "/api/v1/team", None, True),
    ("GET", "/api/v1/reports/rating-trend", None, True),
    ("GET", "/api/v1/quality", None, True),
    ("PATCH", "/api/v1/quality/{session_id}", {}, True),
    ("POST", "/api/v1/exports", {"report": "daily", "format": "csv"}, True),
    ("GET", "/api/v1/exports/{job_id}", None, True),
    ("GET", "/api/v1/exports/{job_id}/download", None, True),
    # Training studio (Phase 11). Archiving and hiding from the app are Admin-only fields: test_studio.py.
    ("GET", "/api/v1/trainings", None, True),
    ("POST", "/api/v1/trainings", {"training_id": "perm_test", "title": "P"}, True),
    ("GET", "/api/v1/trainings/{training_id}", None, True),
    ("PATCH", "/api/v1/trainings/{training_id}", {"title": "P"}, True),
    ("GET", "/api/v1/trainings/{training_id}/versions", None, True),
    ("POST", "/api/v1/trainings/{training_id}/versions", {"source": "blank"}, True),
    ("GET", "/api/v1/versions/{version_id}/content", None, True),
    ("PUT", "/api/v1/versions/{version_id}/content", {"revision": 1, "content": {}}, True),
    ("GET", "/api/v1/versions/{from_id}/diff/{to_id}", None, True),
    # Uploads and prepare for voice (Phase 12).
    (
        "POST",
        "/api/v1/trainings/{training_id}/uploads/presign",
        {"filename": "a.txt", "content_type": "text/plain", "size_bytes": 5},
        True,
    ),
    ("GET", "/api/v1/trainings/{training_id}/uploads", None, True),
    ("POST", "/api/v1/uploads/{upload_id}/complete", None, True),
    ("GET", "/api/v1/uploads/{upload_id}", None, True),
    ("GET", "/api/v1/uploads/{upload_id}/text", None, True),
    ("POST", "/api/v1/versions/{version_id}/prepare", None, True),
    ("GET", "/api/v1/versions/{version_id}/prepare", None, True),
    ("GET", "/api/v1/jobs/{job_id}", None, True),
    ("POST", "/api/v1/versions/{version_id}/validate", None, True),
    ("POST", "/api/v1/versions/{version_id}/preview-call", {}, True),
    ("GET", "/api/v1/versions/{version_id}/readiness", None, True),
    ("POST", "/api/v1/versions/{version_id}/submit", None, True),
    ("POST", "/api/v1/versions/{version_id}/send-back", {}, True),
    ("POST", "/api/v1/versions/{version_id}/publish", {}, True),
    ("GET", "/api/v1/versions/{version_id}/publish", None, True),
    # Voice samples (SPEC 10.3).
    ("GET", "/api/v1/voices", None, True),
    ("GET", "/api/v1/setups", None, True),
    ("POST", "/api/v1/voices/{voice_id}/sample", {"text": "Hi"}, True),
    # Voices, setups and testers (SPEC 10.3, Phase 14): Admin only. Polly is faked below.
    ("GET", "/api/v1/admin/voices", None, False),
    ("PATCH", "/api/v1/admin/voices/{voice_id}", {"notes": "x"}, False),
    ("POST", "/api/v1/admin/voices/{voice_id}/default", None, False),
    ("GET", "/api/v1/admin/voices/available", None, False),
    ("POST", "/api/v1/admin/voices", {"voice_id": "Nobody"}, False),
    ("GET", "/api/v1/admin/profiles", None, False),
    ("PATCH", "/api/v1/admin/profiles/{profile_id}", {"notes": "x"}, False),
    ("POST", "/api/v1/admin/profiles/{profile_id}/default", None, False),
    ("GET", "/api/v1/admin/testers", None, False),
    ("POST", "/api/v1/admin/testers", {"uid": 9, "name": "P", "trainings": ["big4"]}, False),
    ("PATCH", "/api/v1/admin/testers/{tester_id}", {"name": "P"}, False),
    ("POST", "/api/v1/admin/testers/{tester_id}/new-code", None, False),
    ("POST", "/api/v1/admin/testers/import", {"testers": []}, False),
    ("GET", "/api/v1/admin/reference-sync", None, False),
    ("POST", "/api/v1/admin/reference-sync/code", None, False),
    ("POST", "/api/v1/admin/reference-sync", {"code": "123456"}, False),
    # Assigning from the dashboard (2026-10-07), with the Trainer defaults; the switches: test_assignments.py.
    ("GET", "/api/v1/me/permissions", None, True),
    ("GET", "/api/v1/admin/permissions", None, False),
    (
        "PUT",
        "/api/v1/admin/permissions",
        {"changes": [{"action": "assign_bulk", "role": "trainer", "enabled": False}]},
        False,
    ),
    ("GET", "/api/v1/assignments/people?q=ab", None, True),
    ("GET", "/api/v1/assignments/trainings", None, True),
    ("POST", "/api/v1/assignments/check", {"uid": 1001, "training_ids": ["big4"]}, True),
    ("POST", "/api/v1/assignments", {"uid": 1001, "training_ids": ["big4"]}, True),
    ("POST", "/api/v1/assignments/cancel", {"assignment_ids": [999999]}, True),
    ("POST", "/api/v1/assignments/due-date", {"assignment_ids": [999999], "due_date": None}, True),
]


@pytest.fixture(autouse=True)
def _no_polly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.studio import admin

    monkeypatch.setattr(admin, "polly_voices", lambda _settings: [])


# The Flutter app's routes (docs/APP_HANDOFF.md): a NudgeLab pass only. Dashboard logins, Trainer or Admin,
# get 401. The pass itself is tested in test_app_api.py.
APP_ROUTES: list[tuple[str, str]] = [
    ("GET", "/app/v1/trainings"),
    ("GET", "/app/v1/trainings/pending-count"),
    ("POST", "/app/v1/trainings/{training_id}/session"),
]


def _call(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> int:
    path = path.replace("{user_id}", "1").replace("{training_key}", "big4").replace("{uid}", "1001")
    path = path.replace("{session_id}", "s1").replace("{job_id}", "1").replace("{view_id}", "999999")
    path = path.replace("{training_id}", "big4").replace("{version_id}", "999999")
    path = path.replace("{upload_id}", "999999").replace("{voice_id}", "Nobody")
    path = path.replace("{from_id}", "999998").replace("{to_id}", "999999")
    path = path.replace("{profile_id}", "nobody").replace("{tester_id}", "999999")
    return client.request(method, path, json=body, headers=headers or {}).status_code


def test_every_route_is_in_the_matrix() -> None:
    routes = set()
    for path, ops in app.openapi()["paths"].items():
        routes |= {(m.upper(), path) for m in ops}
    covered = PUBLIC | {(m, p.split("?")[0]) for m, p, _, _ in PROTECTED} | set(APP_ROUTES)
    assert routes == covered, f"not covered: {routes - covered}; stale: {covered - routes}"


@pytest.mark.parametrize(("method", "path", "body", "_trainer_ok"), PROTECTED)
def test_anonymous_gets_401(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None, _trainer_ok: bool
) -> None:
    assert _call(client, method, path, body) == 401


@pytest.mark.parametrize(("method", "path", "body", "trainer_ok"), PROTECTED)
def test_trainer_access(
    client: TestClient,
    trainer_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    trainer_ok: bool,
) -> None:
    status = _call(client, method, path, body, trainer_headers)
    if trainer_ok:
        assert status not in (401, 403), status
    else:
        assert status == 403


@pytest.mark.parametrize(("method", "path", "body", "_trainer_ok"), PROTECTED)
def test_admin_is_never_forbidden(
    client: TestClient,
    admin_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
    _trainer_ok: bool,
) -> None:
    assert _call(client, method, path, body, admin_headers) not in (401, 403)


@pytest.fixture
def app_passes_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    pem = tmp_path / "pass.pem"
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    pem.write_bytes(key.public_bytes(serialization.Encoding.PEM, spki))
    monkeypatch.setattr(get_settings(), "app_pass_public_keys", f"k={pem}")


@pytest.mark.parametrize(("method", "path"), APP_ROUTES)
def test_app_routes_refuse_anonymous_and_dashboard_logins(
    client: TestClient,
    trainer_headers: dict[str, str],
    admin_headers: dict[str, str],
    app_passes_configured: None,
    method: str,
    path: str,
) -> None:
    for headers in (None, trainer_headers, admin_headers):
        assert _call(client, method, path, None, headers) == 401
