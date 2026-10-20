"""One codebase, several companies (2026-10-20): the company's name and wording come from each
installation's Admin > Settings (company.*), defaulting to Prime's, and reach the AI prompts, new trainings
and the dashboard."""

from __future__ import annotations

from typing import Any

from app.services import settings_table
from app.services.company import PRIME, company
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def suncom(db: Session) -> None:
    for key, value in {
        "company.name": "Suncom Mobile",
        "company.description": "a wireless retailer",
        "company.app_name": "the Suncom website",
        "company.portal_name": "Suncom Portal",
        "company.email_domain": "suncom.example",
    }.items():
        db.execute(text("UPDATE dash_settings SET value = :v WHERE `key` = :k"), {"v": value, "k": key})
    settings_table.clear_cache()


def test_prime_is_the_default_and_nothing_changes_for_it(db_session: Session) -> None:
    settings_table.clear_cache()
    assert company(db_session) == PRIME  # seeded by migration 0019 with Prime's values


def test_another_installation_says_its_own_name(
    client: TestClient, trainer_headers: dict[str, str], admin_headers: dict[str, str], db_session: Session
) -> None:
    assert client.get("/api/v1/company", headers=trainer_headers).json()["portal_name"] == "Prime Portal"
    suncom(db_session)
    shown = client.get("/api/v1/company", headers=trainer_headers).json()
    assert shown == {
        "name": "Suncom Mobile",
        "description": "a wireless retailer",
        "app_name": "the Suncom website",
        "portal_name": "Suncom Portal",
        "email_domain": "suncom.example",
    }
    # Admins edit it on Admin > Settings like any other setting.
    put = client.put("/api/v1/admin/settings/company.name", headers=admin_headers, json={"value": "Suncom"})
    assert put.status_code == 200 and company(db_session).name == "Suncom"
    settings_table.clear_cache()


def test_the_ai_prompts_and_new_trainings_use_it(db_session: Session, monkeypatch: Any) -> None:
    from app.studio.blank import blank_content
    from app.summary import narrative

    suncom(db_session)
    who = company(db_session)
    content = blank_content(
        title="T", completion_type="quiz", uses_location=False, trainer_name="Anne", company=who.name
    )
    assert content["training"]["company"] == "Suncom Mobile"
    sent: dict[str, Any] = {}

    class Client:
        def __init__(self, **_: Any) -> None:
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            block = type("B", (), {"type": "tool_use", "name": "submit", "input": {"headline": "x"}})()
            return type("M", (), {"content": [block], "stop_reason": "tool_use"})()

    import anthropic

    monkeypatch.setattr(anthropic, "AnthropicBedrock", Client)
    from app.config import get_settings

    narrative.call_claude(get_settings(), {"calls": 3, "company": who.as_dict()})
    assert "for Suncom Mobile's" in sent["system"] and "from the Suncom website" in sent["system"]
    assert '"company"' not in sent["messages"][0]["content"]  # not passed off as one of the figures
    settings_table.clear_cache()
