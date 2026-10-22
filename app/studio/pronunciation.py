"""Hear how a training's acronyms will be spoken (2026-10-23): the Studio's "Check pronunciation".

In a real call the agent rewrites every run of 2 to 6 capital letters as spaced letters before the voice
reads it ("PPVGA" becomes "P P V G A"; AT&T is left alone), because Polly would say "AIP" like "ape". This is
the same rule, so a preview sounds like the call. KEEP IT IN STEP with `spell_out_acronyms` in the agent's
training.py: both repos test the same table of cases (tests/test_pronunciation.py here,
tests/test_acronyms.py there).

`scan` lists the acronyms in a draft's content, each with how it will be said and a sentence it appears in, so
a trainer can play it. Only what could be spoken is scanned: the trainee's expected answers are not.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

ACRONYM = re.compile(r"(?<![\w&])([A-Z]{2,6})(?![\w&])")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
MAX_ACRONYMS = 150
MAX_EXAMPLE = 220
# Knowledge-base lines the trainee says or that are only a check (never spoken by the trainer).
_NOT_SPOKEN = {"expected", "accept"}


def spoken(text: str) -> str:
    """The text as the voice will be given it: acronyms spelled out letter by letter."""
    return ACRONYM.sub(lambda m: " ".join(m.group(1)), text)


def _texts(content: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """(where, text) for everything in the content that the trainer might say."""
    training = content.get("training") or {}
    name = str(training.get("trainer_name") or "")

    def fill(text: Any) -> str:
        return str(text or "").replace("{trainer_name}", name)

    yield "Title", fill(training.get("title"))
    for key, line in (training.get("lines") or {}).items():
        yield f"Line {key}", fill(line)
    for i, step in enumerate(training.get("opening") or [], 1):
        if isinstance(step, dict):
            for part in ("say", "ask", "otherwise"):
                yield f"Opening step {i}", fill(step.get(part))
    kb = content.get("knowledge_base") or {}
    for line in kb.get("preamble") or []:
        if line.get("kind") not in _NOT_SPOKEN:
            yield "Introduction", fill(line.get("text"))
    for topic in kb.get("topics") or []:
        for line in topic.get("lines") or []:
            if line.get("kind") not in _NOT_SPOKEN:
                yield f"Topic {topic.get('number')}", fill(line.get("text"))
    for q in (content.get("quiz") or {}).get("questions") or []:
        bodies = list((q.get("variants") or {}).values()) or [q]
        for body in bodies:
            yield f"Quiz question {q.get('number')}", fill(body.get("question"))
            for option in (body.get("options") or {}).values():
                yield f"Quiz question {q.get('number')}", fill(option)
    roleplay = content.get("roleplay") or {}
    for item in (roleplay.get("coach") or {}).get("items") or []:
        yield f"Coaching: {item.get('title')}", fill(item.get("probe"))
        yield f"Coaching: {item.get('title')}", fill(item.get("teach"))


def _sentence(text: str, start: int) -> str:
    """The sentence of `text` around position `start`, trimmed."""
    pos = 0
    for part in _SENTENCE_END.split(text):
        end = pos + len(part)
        if pos <= start <= end:
            return part.strip()[:MAX_EXAMPLE]
        pos = end + 1
    return text.strip()[:MAX_EXAMPLE]


def scan(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Every acronym in the content: how often, where it first appears, a sentence to play, how it's said."""
    found: dict[str, dict[str, Any]] = {}
    count: dict[str, int] = defaultdict(int)
    for where, text in _texts(content):
        for m in ACRONYM.finditer(text):
            acronym = m.group(1)
            count[acronym] += 1
            if acronym not in found:
                found[acronym] = {
                    "acronym": acronym,
                    "spoken": " ".join(acronym),
                    "where": where,
                    "example": _sentence(text, m.start()),
                }
    rows = [{**found[a], "count": count[a]} for a in found]
    rows.sort(key=lambda r: (-r["count"], r["acronym"]))
    return rows[:MAX_ACRONYMS]
