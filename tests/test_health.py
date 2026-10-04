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


def test_cors_lets_the_dashboard_read_export_file_names(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "Content-Disposition" in response.headers["access-control-expose-headers"]


def test_slow_requests_are_logged(client, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    """Requests over SLOW_REQUEST_MS are logged with their path, never their query string."""
    from app.utils import request_context

    logged: list[dict[str, object]] = []
    monkeypatch.setattr(request_context, "SLOW_REQUEST_MS", 0)
    monkeypatch.setattr(
        request_context.logger, "warning", lambda event, **kw: logged.append({"event": event, **kw})
    )
    client.get("/api/v1/health?search=Jane")
    assert logged and logged[0]["event"] == "slow_request"
    assert logged[0]["path"] == "/api/v1/health" and "Jane" not in str(logged[0])
