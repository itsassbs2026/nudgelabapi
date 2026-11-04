"""How the API reaches Claude: Amazon Bedrock (Prime), or Anthropic's own API where ANTHROPIC_API_KEY is set.

2026-11-03: AWS declined Bedrock quota for Suncom's new AWS account, so Suncom calls Anthropic directly.
Model ids stay in their Bedrock form in settings and the settings table ("us.anthropic.claude-sonnet-5-5");
model() gives the name the chosen route expects. Used by Ask NudgeLab, "Prepare for voice" and the daily
summary.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings


def direct(settings: Settings) -> bool:
    return bool(settings.anthropic_api_key)


def client(settings: Settings, *, timeout: float = 120) -> Any:
    """An Anthropic SDK client: Anthropic's API when a key is set, else Bedrock through the instance role."""
    import anthropic

    if direct(settings):
        return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout, max_retries=2)
    return anthropic.AnthropicBedrock(aws_region=settings.bedrock_region, timeout=timeout, max_retries=2)


def model(settings: Settings, model_id: str) -> str:
    """The model id as the route names it: Bedrock's as given, Anthropic's without the route, provider and
    version ("us.anthropic.claude-haiku-4-5-20251001-v1:0" -> "claude-haiku-4-5-20251001")."""
    if not direct(settings):
        return model_id
    return re.sub(r"-v\d+:\d+$", "", re.sub(r"^(?:[a-z]+\.)?anthropic\.", "", model_id))
