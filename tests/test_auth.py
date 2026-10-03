"""Sign-in, sessions and passwords (SPEC §9)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.models.dashboard import DashAuditLog, DashEmailOutbox, DashRefreshToken, DashUser
from app.services.auth import FAILED_LOGIN_THRESHOLD
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import GOOD_PASSWORD

LOGIN = "/api/v1/auth/login"


def _actions(db: Session, user_id: int | None = None) -> list[str]:
    q = select(DashAuditLog.action).order_by(DashAuditLog.id)
    if user_id is not None:
        q = q.where(DashAuditLog.actor_user_id == user_id)
    return list(db.execute(q).scalars())


# -- login -----------------------------------------------------------------------------------------


def test_login_returns_token_user_and_refresh_cookie(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    user = make_user(email="Ann@Example.com".lower())
    response = client.post(LOGIN, json={"email": "ANN@example.com", "password": GOOD_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["expires_in"] == 15 * 60
    assert body["user"]["email"] == "ann@example.com" and "password_hash" not in body["user"]
    cookie = response.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/api/v1/auth" in cookie
    assert "samesite=strict" in cookie.lower()
    assert "login" in _actions(db_session, user.id)


def test_wrong_password_and_unknown_email_look_the_same(
    client: TestClient, make_user: Callable[..., DashUser]
) -> None:
    make_user(email="bob@example.com")
    wrong = client.post(LOGIN, json={"email": "bob@example.com", "password": "nope-nope-nope"})
    unknown = client.post(LOGIN, json={"email": "ghost@example.com", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_inactive_user_cannot_log_in(client: TestClient, make_user: Callable[..., DashUser]) -> None:
    make_user(email="gone@example.com", is_active=False)
    response = client.post(LOGIN, json={"email": "gone@example.com", "password": GOOD_PASSWORD})
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_credentials"


def test_lockout_after_five_failures_then_unlocks(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    user = make_user(email="carl@example.com")
    for _ in range(FAILED_LOGIN_THRESHOLD):
        client.post(LOGIN, json={"email": "carl@example.com", "password": "wrong-password-x"})
    locked = client.post(LOGIN, json={"email": "carl@example.com", "password": GOOD_PASSWORD})
    assert locked.status_code == 401 and locked.json()["error"]["code"] == "account_locked"
    assert "account_locked" in _actions(db_session, user.id)

    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)  # the 15 minutes have passed
    db_session.flush()
    assert (
        client.post(LOGIN, json={"email": "carl@example.com", "password": GOOD_PASSWORD}).status_code == 200
    )
    db_session.refresh(user)
    assert user.failed_login_count == 0 and user.locked_until is None


def test_login_is_rate_limited(client: TestClient) -> None:
    codes = [
        client.post(LOGIN, json={"email": "x@example.com", "password": "whatever-123"}).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429


# -- refresh, logout -------------------------------------------------------------------------------


def test_refresh_rotates_and_reuse_revokes_the_family(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    make_user(email="dee@example.com")
    client.post(LOGIN, json={"email": "dee@example.com", "password": GOOD_PASSWORD})
    first = client.cookies.get("refresh_token")
    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200 and rotated.json()["access_token"]
    second = client.cookies.get("refresh_token")
    assert second and second != first

    # Someone presents the old (already rotated) token: the whole family is revoked, including the new one.
    client.cookies.set("refresh_token", first or "", path="/api/v1/auth")
    reused = client.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    client.cookies.set("refresh_token", second or "", path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert "refresh_token_reused" in _actions(db_session)


def test_refresh_without_cookie_or_after_expiry_fails(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    assert client.post("/api/v1/auth/refresh").status_code == 401
    make_user(email="eve@example.com")
    client.post(LOGIN, json={"email": "eve@example.com", "password": GOOD_PASSWORD})
    for row in db_session.execute(select(DashRefreshToken)).scalars():
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_fails_for_a_deactivated_user(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    user = make_user(email="fay@example.com")
    client.post(LOGIN, json={"email": "fay@example.com", "password": GOOD_PASSWORD})
    user.is_active = False
    db_session.flush()
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_revokes_the_session(
    client: TestClient, make_user: Callable[..., DashUser], login: Callable[..., dict[str, str]]
) -> None:
    make_user(email="gus@example.com")
    headers = login("gus@example.com")
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_deactivated_user_is_locked_out_at_once(
    client: TestClient,
    make_user: Callable[..., DashUser],
    login: Callable[..., dict[str, str]],
    db_session: Session,
) -> None:
    user = make_user(email="hal@example.com")
    headers = login("hal@example.com")
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    user.is_active = False
    db_session.flush()
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_garbage_and_missing_tokens_are_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


# -- forced password change, change password -------------------------------------------------------


def test_pending_password_change_blocks_everything_but_me_change_and_logout(
    client: TestClient, make_user: Callable[..., DashUser], login: Callable[..., dict[str, str]]
) -> None:
    make_user(email="ivy@example.com", role="admin", must_change_password=True)
    headers = login("ivy@example.com")
    assert client.get("/api/v1/me", headers=headers).json()["must_change_password"] is True
    blocked = client.get("/api/v1/admin/users", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"
    assert client.patch("/api/v1/me", json={"timezone": "UTC"}, headers=headers).status_code == 403

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": GOOD_PASSWORD, "new_password": "a-brand-new-password"},
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 200


def test_change_password_rules(
    client: TestClient, make_user: Callable[..., DashUser], login: Callable[..., dict[str, str]]
) -> None:
    make_user(email="jo@example.com")
    headers = login("jo@example.com")
    url = "/api/v1/auth/change-password"
    wrong = client.post(
        url, headers=headers, json={"current_password": "wrong-one-xx", "new_password": "x" * 14}
    )
    assert wrong.json()["error"]["code"] == "invalid_current_password"
    short = client.post(
        url, headers=headers, json={"current_password": GOOD_PASSWORD, "new_password": "short"}
    )
    assert short.status_code == 422 and short.json()["error"]["code"] == "weak_password"
    common = client.post(
        url, headers=headers, json={"current_password": GOOD_PASSWORD, "new_password": "password1234"}
    )
    assert common.json()["error"]["code"] == "weak_password"
    same = client.post(
        url, headers=headers, json={"current_password": GOOD_PASSWORD, "new_password": GOOD_PASSWORD}
    )
    assert same.json()["error"]["code"] == "same_password"


# -- forgot / reset --------------------------------------------------------------------------------


def test_forgot_password_never_reveals_accounts(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    make_user(email="kim@example.com")
    assert (
        client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}).status_code == 204
    )
    assert client.post("/api/v1/auth/forgot-password", json={"email": "kim@example.com"}).status_code == 204
    outbox = db_session.execute(select(DashEmailOutbox)).scalars().all()
    assert [m.to_email for m in outbox] == ["kim@example.com"]
    assert "/reset-password?token=" in outbox[0].body_html


def test_reset_password_with_a_valid_link_once(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    from app.config import get_settings
    from app.services.auth import enqueue_password_reset_email

    user = make_user(email="lee@example.com")
    link = enqueue_password_reset_email(db_session, get_settings(), user)
    token = link.split("token=")[1]
    url = "/api/v1/auth/reset-password"
    assert client.post(url, json={"token": token, "new_password": "short"}).status_code == 422
    assert client.post(url, json={"token": token, "new_password": "new-password-for-lee"}).status_code == 204
    again = client.post(url, json={"token": token, "new_password": "another-password-lee"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_or_expired_token"
    ok = client.post(LOGIN, json={"email": "lee@example.com", "password": "new-password-for-lee"})
    assert ok.status_code == 200


def test_reset_link_expires(
    client: TestClient, make_user: Callable[..., DashUser], db_session: Session
) -> None:
    from app.config import get_settings
    from app.models.dashboard import DashPasswordResetToken
    from app.services.auth import enqueue_password_reset_email

    user = make_user(email="max@example.com")
    token = enqueue_password_reset_email(db_session, get_settings(), user).split("token=")[1]
    for row in db_session.execute(select(DashPasswordResetToken)).scalars():
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    response = client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "new-password-max-1"}
    )
    assert response.status_code == 400


# -- me --------------------------------------------------------------------------------------------


def test_me_update_timezone_and_preferences(
    client: TestClient, make_user: Callable[..., DashUser], login: Callable[..., dict[str, str]]
) -> None:
    make_user(email="ned@example.com")
    headers = login("ned@example.com")
    updated = client.patch(
        "/api/v1/me", headers=headers, json={"timezone": "America/New_York", "preferences": {"theme": "dark"}}
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "America/New_York" and updated.json()["preferences"] == {
        "theme": "dark"
    }
    bad = client.patch("/api/v1/me", headers=headers, json={"timezone": "Mars/Olympus"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_timezone"
