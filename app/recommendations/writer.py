"""Claude turns one training's evidence (evidence.py) into recommendations a trainer can act on in the
content, plus a short list for the NudgeLab team of what the content can't fix.

One request per training per night. Claude must cite the figures it was given and may only name sessions that
are in the evidence; `clean` drops anything else, so what reaches the Studio is checked, not just trusted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

AREAS = ("question", "options", "topic", "opening", "length", "wording", "pronunciation", "images", "other")
IMPACTS = ("high", "medium", "low")
MAX_ITEMS = 5

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "enum": list(AREAS)},
                    "target": {"type": "string", "description": 'Where: "Question 3", "Topic 2", "Opening".'},
                    "impact": {"type": "string", "enum": list(IMPACTS)},
                    "title": {"type": "string", "description": "Under 12 words."},
                    "problem": {
                        "type": "string",
                        "description": "What goes wrong for trainees, 1-2 sentences.",
                    },
                    "suggestion": {
                        "type": "string",
                        "description": "The change to make in the content, concrete (new wording helps).",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "The figures behind it, from the data only.",
                    },
                    "example_session_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["area", "target", "impact", "title", "problem", "suggestion", "evidence"],
            },
        },
        "ours": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                    "example_session_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["issue", "evidence"],
            },
        },
    },
    "required": ["recommendations", "ours"],
}

SYSTEM = """You review a week of voice-training calls for one training and tell its trainers what to change in
the training's CONTENT. Employees call an AI trainer (NudgeLab) that teaches the training's topics, then asks
its quiz questions out loud; trainees answer by voice (a letter, or the answer's words).

The trainer can change only the content: the topics' text and order, the opening, the check-in questions, the
quiz questions and their options and correct answer, explanations, pronunciations of terms, images, and
length.
They can't change the AI trainer's behaviour, speech recognition, the app, or our grading code.

recommendations (at most 5, the most useful first; fewer is fine, none if nothing stands out):
- Only things fixable in the content. Each one says what goes wrong for trainees, the change to make (with new
  wording when that helps), and the evidence: figures from the data only (counts, rates, quotes). Never invent
  or estimate a number.
- Look for: quiz questions many miss on the first try, especially when one wrong option draws most of them
  (options too alike, a misleading option, a question the topics don't teach, or a correct answer that may be
  wrong); options that are long or hard to say out loud; topics where people hang up or that take much longer
  than the others; terms trainees ask about or say "what does that mean"; places they ask to see something
  (an image could help); complaints about length or clarity in rating quotes.
- When trainees' spoken answers show they knew the right answer but were graded wrong (for example the right
  option's words, said differently), that's our grading, not the content: put it in ours. You may still
  suggest
  options that are easier to tell apart out loud.
- example_session_ids: up to 3 session_id values copied exactly from the data.
- Don't repeat an earlier recommendation that was dismissed, unless the evidence is now much stronger (say
why).
  Done ones may come back only if the problem is still there after the change.

ours (at most 5): problems the content can't fix, for the NudgeLab team: speech recognition mishearing letters
or words, answers graded wrong though right, the AI trainer behaving badly (talking over people, wrong praise,
skipping), app or connection problems, slowness. Same rules for evidence. Leave out general dislike of AI.

Plain words, no jargon. When you're done, call the submit tool once. Write nothing else."""

Writer = Callable[[Settings, dict[str, Any], list[dict[str, Any]]], tuple[dict[str, Any], dict[str, int]]]


def call_claude(
    settings: Settings, evidence: dict[str, Any], earlier: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, int]]:
    from anthropic import AnthropicBedrock
    from anthropic.types import ToolParam

    client = AnthropicBedrock(aws_region=settings.bedrock_region, timeout=300, max_retries=2)
    tool: ToolParam = {
        "name": "submit",
        "description": "Submit the recommendations, once.",
        "input_schema": SCHEMA,
    }
    message = client.messages.create(
        model=settings.recommendations_model,
        max_tokens=6000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": "Earlier recommendations and what trainers did with them:\n"
                + json.dumps(earlier, default=str)
                + "\n\nThis week's evidence:\n"
                + json.dumps(evidence, default=str, ensure_ascii=False),
            }
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit"},
    )
    submitted = next((b.input for b in message.content if b.type == "tool_use" and b.name == "submit"), None)
    if not isinstance(submitted, dict):
        raise ValueError(f"nothing submitted (stop_reason {message.stop_reason})")
    usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens}
    return submitted, usage


# Tests replace this with a fake.
writer: Writer = call_claude


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def session_ids(evidence: dict[str, Any]) -> set[str]:
    """Every session id the evidence mentions: the only ones a recommendation may point at."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "session_id" and isinstance(x, str):
                    found.add(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk({k: v for k, v in evidence.items() if k != "content"})
    return found


def clean(got: dict[str, Any], evidence: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only well-formed items, with examples from the evidence."""
    known = session_ids(evidence)

    def examples(item: dict[str, Any]) -> list[str]:
        raw = item.get("example_session_ids")
        return [s for s in raw if isinstance(s, str) and s in known][:3] if isinstance(raw, list) else []

    recs = []
    for item in got.get("recommendations") or []:
        if not isinstance(item, dict):
            continue
        rec = {
            "area": item.get("area") if item.get("area") in AREAS else "other",
            "target": _text(item.get("target"), 80) or None,
            "impact": item.get("impact") if item.get("impact") in IMPACTS else "medium",
            "title": _text(item.get("title"), 200),
            "problem": _text(item.get("problem"), 2000),
            "suggestion": _text(item.get("suggestion"), 3000),
            "evidence": _text(item.get("evidence"), 2000),
            "examples": examples(item),
        }
        if rec["title"] and rec["problem"] and rec["suggestion"] and rec["evidence"]:
            recs.append(rec)
    ours = []
    for item in got.get("ours") or []:
        if isinstance(item, dict) and _text(item.get("issue"), 600) and _text(item.get("evidence"), 1200):
            ours.append(
                {
                    "issue": _text(item.get("issue"), 600),
                    "evidence": _text(item.get("evidence"), 1200),
                    "examples": examples(item),
                }
            )
    return recs[:MAX_ITEMS], ours[:MAX_ITEMS]
