"""Claude through Anthropic's API where ANTHROPIC_API_KEY is set (Suncom, 2026-11-03), else Bedrock
(Prime)."""

from __future__ import annotations

from typing import Any

import anthropic
import pytest
from app import claude
from app.config import Settings, get_settings
from app.summary import narrative


def settings(key: str = "") -> Settings:
    return get_settings().model_copy(update={"anthropic_api_key": key})


def test_without_a_key_claude_is_on_bedrock() -> None:
    s = settings()
    assert isinstance(claude.client(s), anthropic.AnthropicBedrock)
    assert claude.model(s, "us.anthropic.claude-sonnet-5-5") == "us.anthropic.claude-sonnet-5-5"


def test_with_a_key_claude_is_anthropics_api() -> None:
    s = settings("test-key")
    c = claude.client(s, timeout=900)
    assert type(c) is anthropic.Anthropic and c.timeout == 900
    assert claude.model(s, "us.anthropic.claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert claude.model(s, "us.anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    assert claude.model(s, "claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_the_daily_summary_is_written_through_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            sent["client"] = kwargs
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            block = type("B", (), {"type": "tool_use", "name": "submit", "input": {"headline": "x"}})()
            return type("M", (), {"content": [block], "stop_reason": "tool_use"})()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    assert narrative.call_claude(settings("test-key"), {"calls": 3}) == {"headline": "x"}
    assert sent["client"]["api_key"] == "test-key"
    assert sent["model"] == "claude-sonnet-5-5"
