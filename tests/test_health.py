from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "version" in body
    assert "X-Request-ID" in response.headers


def test_health_echoes_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-request-id-123"})
    assert response.headers["X-Request-ID"] == "test-request-id-123"


def test_unknown_route_uses_the_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
