"""User management and the audit log (SPEC §4): what Admins can do, and the safeguards."""

from __future__ import annotations

from collections.abc import Callable

from app.models.dashboard import DashEmailOutbox, DashUser
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import GOOD_PASSWORD

USERS = "/api/v1/admin/users"


def test_create_user_with_temporary_password(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = client.post(
        USERS,
        headers=admin_headers,
        json={
            "email": "New.Trainer@Example.com",
            "full_name": "New Trainer",
            "role": "trainer",
            "temporary_password": "temporary-pass-123",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "new.trainer@example.com" and body["role"] == "trainer"
    assert body["must_change_password"] is True
    signed_in = client.post(
        "/api/v1/auth/login", json={"email": "new.trainer@example.com", "password": "temporary-pass-123"}
    )
    assert signed_in.status_code == 200 and signed_in.json()["user"]["must_change_password"] is True


def test_create_user_by_invitation_email(
    client: TestClient, admin_headers: dict[str, str], db_session: Session
) -> None:
    created = client.post(
        USERS, headers=admin_headers, json={"email": "invitee@example.com", "full_name": "Invitee"}
    )
    assert created.status_code == 201
    mail = db_session.execute(select(DashEmailOutbox).filter_by(to_email="invitee@example.com")).scalar_one()
    assert mail.subject.startswith("Set your") and "/reset-password?token=" in mail.body_html


def test_create_user_validation(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.post(
        USERS,
        headers=admin_headers,
        json={"email": "dup@example.com", "full_name": "Dup", "temporary_password": "temporary-pass-123"},
    )
    dup = client.post(
        USERS,
        headers=admin_headers,
        json={"email": "DUP@example.com", "full_name": "Dup 2", "temporary_password": "temporary-pass-123"},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "email_taken"
    weak = client.post(
        USERS,
        headers=admin_headers,
        json={"email": "w@example.com", "full_name": "W", "temporary_password": "short"},
    )
    assert weak.status_code == 422
    bad_role = client.post(
        USERS, headers=admin_headers, json={"email": "r@example.com", "full_name": "R", "role": "superuser"}
    )
    assert bad_role.status_code == 422


def test_list_search_and_filter(
    client: TestClient, admin_headers: dict[str, str], make_user: Callable[..., DashUser]
) -> None:
    make_user(email="zoe.trainer@example.com")
    make_user(email="old@example.com", is_active=False)
    everyone = client.get(USERS, headers=admin_headers).json()
    assert everyone["total"] == 3
    assert client.get(USERS, headers=admin_headers, params={"search": "zoe"}).json()["total"] == 1
    assert client.get(USERS, headers=admin_headers, params={"is_active": "false"}).json()["total"] == 1
    assert client.get(USERS, headers=admin_headers, params={"role": "admin"}).json()["total"] == 1


def test_deactivate_ends_sessions_and_reactivate(
    client: TestClient,
    admin_headers: dict[str, str],
    make_user: Callable[..., DashUser],
    login: Callable[..., dict[str, str]],
) -> None:
    trainer = make_user(email="tia@example.com")
    trainer_headers = login("tia@example.com")
    off = client.patch(f"{USERS}/{trainer.id}", headers=admin_headers, json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert client.get("/api/v1/me", headers=trainer_headers).status_code == 401
    on = client.patch(f"{USERS}/{trainer.id}", headers=admin_headers, json={"is_active": True})
    assert on.json()["is_active"] is True
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "tia@example.com", "password": GOOD_PASSWORD}
        ).status_code
        == 200
    )


def test_cannot_demote_or_deactivate_yourself_or_the_last_admin(
    client: TestClient, admin_headers: dict[str, str], db_session: Session, make_user: Callable[..., DashUser]
) -> None:
    me = db_session.execute(select(DashUser).filter_by(email="admin@example.com")).scalar_one()
    demote_self = client.patch(f"{USERS}/{me.id}", headers=admin_headers, json={"role": "trainer"})
    assert (
        demote_self.status_code == 409 and demote_self.json()["error"]["code"] == "cannot_change_own_access"
    )
    off_self = client.patch(f"{USERS}/{me.id}", headers=admin_headers, json={"is_active": False})
    assert off_self.status_code == 409

    other_admin = make_user(role="admin", email="second.admin@example.com")
    assert (
        client.patch(f"{USERS}/{other_admin.id}", headers=admin_headers, json={"role": "trainer"}).status_code
        == 200
    )  # two admins, so one can go


def test_last_admin_rule_with_only_one_admin(db_session: Session, make_user: Callable[..., DashUser]) -> None:
    """The last active Admin can't be demoted, even by a second (inactive) admin's records."""
    import pytest
    from app.services.users import update_user
    from app.utils.errors import ApiError

    only_admin = make_user(role="admin", email="only.admin@example.com")
    acting = make_user(role="admin", email="acting.admin@example.com", is_active=False)
    with pytest.raises(ApiError) as err:
        update_user(db_session, acting, only_admin.id, {"is_active": False}, ip=None)
    assert err.value.code == "last_admin"


def test_admin_reset_with_temporary_password(
    client: TestClient,
    admin_headers: dict[str, str],
    make_user: Callable[..., DashUser],
    login: Callable[..., dict[str, str]],
) -> None:
    user = make_user(email="uma@example.com")
    old_session = login("uma@example.com")
    reset = client.post(
        f"{USERS}/{user.id}/reset-password",
        headers=admin_headers,
        json={"temporary_password": "temp-for-uma-2026"},
    )
    assert reset.status_code == 204
    assert client.post("/api/v1/auth/refresh").status_code == 401  # old sessions ended
    assert client.get("/api/v1/me", headers=old_session).status_code == 200  # access token lives ≤ 15 min
    signed = client.post(
        "/api/v1/auth/login", json={"email": "uma@example.com", "password": "temp-for-uma-2026"}
    )
    assert signed.json()["user"]["must_change_password"] is True


def test_admin_reset_by_email(
    client: TestClient, admin_headers: dict[str, str], make_user: Callable[..., DashUser], db_session: Session
) -> None:
    user = make_user(email="val@example.com")
    assert client.post(f"{USERS}/{user.id}/reset-password", headers=admin_headers, json={}).status_code == 204
    assert db_session.execute(select(DashEmailOutbox).filter_by(to_email="val@example.com")).scalar_one()


def test_audit_log_records_and_filters(
    client: TestClient, admin_headers: dict[str, str], make_user: Callable[..., DashUser]
) -> None:
    user = make_user(email="wes@example.com")
    client.patch(f"{USERS}/{user.id}", headers=admin_headers, json={"full_name": "Wes Updated"})
    entries = client.get(
        "/api/v1/admin/audit", headers=admin_headers, params={"action": "user_updated"}
    ).json()
    assert entries["total"] == 1
    entry = entries["items"][0]
    assert entry["actor_email"] == "admin@example.com" and entry["target_id"] == str(user.id)
    assert entry["details"] == {"changed": ["full_name"]}
    logins = client.get("/api/v1/admin/audit", headers=admin_headers, params={"action": "login"}).json()
    assert logins["total"] >= 1


def test_unknown_user_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.get(f"{USERS}/999999", headers=admin_headers).status_code == 404
    assert client.patch(f"{USERS}/999999", headers=admin_headers, json={"full_name": "x"}).status_code == 404
