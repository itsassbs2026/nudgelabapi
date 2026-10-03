"""Authorization matrix (SPEC §0.3, §12.1).

Every protected route is called as anonymous, Trainer and Admin. A new route must be added here, or
test_every_route_is_in_the_matrix fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.main import app
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
    ("GET", "/api/v1/assignments", None, True),
]


def _call(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> int:
    path = path.replace("{user_id}", "1").replace("{training_key}", "big4").replace("{uid}", "1001")
    return client.request(method, path, json=body, headers=headers or {}).status_code


def test_every_route_is_in_the_matrix() -> None:
    routes = set()
    for path, ops in app.openapi()["paths"].items():
        routes |= {(m.upper(), path) for m in ops}
    covered = PUBLIC | {(m, p) for m, p, _, _ in PROTECTED}
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
