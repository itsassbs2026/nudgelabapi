"""The words in the daily summary: a headline, what went well, what needs attention.

Claude writes them from the figures alone (app/summary/data.py), one request a day. Every number stays in the
code's hands: the prompt says to use only the figures given, and the email's tables show the figures
themselves. If the request fails, or what comes back isn't usable, `fallback` writes plain bullets from rules,
so the email always goes out.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence, under 25 words: the day's main point."},
        "went_well": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "needs_attention": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
    },
    "required": ["headline", "went_well", "needs_attention"],
}

SYSTEM = """You write the morning summary of yesterday's voice-training calls for Prime Communications'
leadership. The trainings run on NudgeLab: employees call an AI trainer from the Prime app, learn a topic, and
pass a short quiz.

Write for a busy executive: plain words, specific, no jargon, no hype. Use ONLY the figures in the data; never
invent or estimate a number, and never do arithmetic beyond simple comparisons the data supports. Name
trainings by their title. Percentages in the data are fractions (0.79 means 79%).

- headline: one sentence, the day's main point, with its key number.
- went_well: 2 or 3 bullets, each one sentence under 25 words.
- needs_attention: 2 or 3 bullets, each one sentence under 25 words, ending with what to do about it when
  that's clear. Calls that never connected, several quick tries by one person, and early hang-ups usually mean
  the person's phone or network; overdue assignments and low pass rates mean follow-up by managers.
If a day has no calls, say so plainly.

When you're done, call the submit tool once with the result. Write nothing else."""

Writer = Callable[[Settings, dict[str, Any]], dict[str, Any]]


def call_claude(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    from anthropic import AnthropicBedrock

    client = AnthropicBedrock(aws_region=settings.bedrock_region, timeout=120, max_retries=2)
    tool = {"name": "submit", "description": "Submit the summary, once.", "input_schema": SCHEMA}
    message = client.messages.create(
        model=settings.summary_model,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Yesterday's figures:\n" + json.dumps(data, default=str)}],
        tools=[tool],  # type: ignore[list-item]
    )
    submitted = next((b.input for b in message.content if b.type == "tool_use" and b.name == "submit"), None)
    if not isinstance(submitted, dict):
        raise ValueError(f"no summary returned (stop_reason {message.stop_reason})")
    return submitted


# Tests replace this with a fake.
writer: Writer = call_claude


def _clean(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [" ".join(str(v).split())[:300] for v in value if isinstance(v, str) and v.strip()][:3]


def count(n: int, word: str, plural: str | None = None) -> str:
    """ "1 call", "2 calls", "1 person", "2 people"."""
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


def pct(value: float | None) -> str:
    return "–" if value is None else f"{round(value * 100)}%"


def fallback(data: dict[str, Any]) -> dict[str, Any]:
    """Plain bullets from rules, when Claude isn't available."""
    k, a, rows = data["kpis"], data["attention"], data["trainings"]
    if not k["calls"]:
        return {"headline": "No training calls yesterday.", "went_well": [], "needs_attention": []}
    passed, calls = count(k["passed"], "person", "people"), count(k["calls"], "call")
    headline = f"{passed} passed a training yesterday, from {calls} ({pct(k['pass_rate'])} of callers)."
    well = []
    if rows:
        top = max(rows, key=lambda r: r["passed"])
        passes = count(top["passed"], "pass", "passes")
        well.append(f"{top['title']} led with {passes} ({pct(top['pass_rate'])} of its callers).")
    if k["avg_rating"] is not None:
        well.append(
            f"Average rating was {k['avg_rating']:.1f} out of 10 from {count(k['ratings'], 'rating')}."
        )
    attention = []
    if a["never_connected"]:
        attention.append(
            f"{count(a['never_connected'], 'call')} never connected, usually the person's phone or network."
        )
    if a["stuck_people"]:
        people, tries = (
            count(a["stuck_people"], "person", "people"),
            count(a["stuck_calls"], "quick try", "quick tries"),
        )
        attention.append(f"{people} made {tries} that went nowhere; they may need help.")
    if a["overdue"]:
        attention.append(f"{count(a['overdue'], 'assignment')} overdue.")
    return {"headline": headline, "went_well": well[:3], "needs_attention": attention[:3]}


def write(settings: Settings, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """(the words, whether Claude wrote them)."""
    if not data["kpis"]["calls"]:
        return fallback(data), False
    try:
        got = writer(settings, data)
        words = {
            "headline": " ".join(str(got.get("headline", "")).split())[:300],
            "went_well": _clean(got.get("went_well")),
            "needs_attention": _clean(got.get("needs_attention")),
        }
        if words["headline"] and words["went_well"] and words["needs_attention"]:
            return words, True
        logger.warning("daily summary: Claude's answer was incomplete; using the rule-based words")
    except Exception as exc:  # noqa: BLE001 - the email must still go out
        logger.warning("daily summary: Claude failed (%s); using the rule-based words", type(exc).__name__)
    return fallback(data), False
