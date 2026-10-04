"""What changed between two versions of a training (SPEC 8.3 `GET /versions/{a}/diff/{b}`).

Structured by the parts a trainer edits, not a text diff of the JSON:
  settings    training.json keys other than `lines` (title, trainer name, opening, acknowledgment, ...)
  lines       each named line (welcome message, closing, ...)
  preamble    the knowledge base text before the first topic
  topics      by topic number (the agent tracks topics by number): added, removed, or changed (title, lines)
  quiz        sections and questions by number
  vocabulary  terms added and removed

Lines inside a topic are compared as a sequence, so an edit shows as the lines removed and the lines added
(`replace`), an insertion as `insert`, a deletion as `delete`. Unchanged parts are left out.
"""

from __future__ import annotations

import difflib
import json
from typing import Any


def _render_line(line: dict[str, Any]) -> str:
    where = f" [{', '.join(line['locations'])}]" if line.get("locations") else ""
    return f"{line.get('prefix', '')}{line.get('text', '')}{where}"


def _line_changes(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a, b = [_render_line(x) for x in old], [_render_line(x) for x in new]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            out.append({"op": op, "from": a[i1:i2], "to": b[j1:j2]})
    return out


def _value_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
            out.append({"key": key, "from": before, "to": after})
    return out


def _topics(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a = {t["number"]: t for t in old}
    b = {t["number"]: t for t in new}
    out = []
    for number in sorted(set(a) | set(b)):
        before, after = a.get(number), b.get(number)
        if before is None and after is not None:
            out.append({"number": number, "change": "added", "title": after["title"].strip(),
                        "lines": _line_changes([], after["lines"])})  # fmt: skip
        elif after is None and before is not None:
            out.append({"number": number, "change": "removed", "title": before["title"].strip(),
                        "lines": _line_changes(before["lines"], [])})  # fmt: skip
        elif before is not None and after is not None:
            lines = _line_changes(before["lines"], after["lines"])
            title_from, title_to = before["title"].strip(), after["title"].strip()
            if lines or title_from != title_to:
                entry: dict[str, Any] = {
                    "number": number,
                    "change": "changed",
                    "title": title_to,
                    "lines": lines,
                }
                if title_from != title_to:
                    entry["title_from"] = title_from
                out.append(entry)
    return out


def _questions(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a = {q["number"]: q for q in old}
    b = {q["number"]: q for q in new}
    out = []
    for number in sorted(set(a) | set(b)):
        before, after = a.get(number), b.get(number)
        if before == after:
            continue
        change = "added" if before is None else "removed" if after is None else "changed"
        out.append({"number": number, "change": change, "from": before, "to": after})
    return out


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_t, new_t = old.get("training", {}), new.get("training", {})
    old_kb, new_kb = old.get("knowledge_base", {}), new.get("knowledge_base", {})
    old_quiz, new_quiz = old.get("quiz") or {}, new.get("quiz") or {}
    old_vocab, new_vocab = set(old.get("vocabulary") or []), set(new.get("vocabulary") or [])
    result: dict[str, Any] = {
        "settings": _value_changes(
            {k: v for k, v in old_t.items() if k != "lines"}, {k: v for k, v in new_t.items() if k != "lines"}
        ),
        "lines": _value_changes(old_t.get("lines", {}), new_t.get("lines", {})),
        "preamble": _line_changes(old_kb.get("preamble", []), new_kb.get("preamble", [])),
        "topics": _topics(old_kb.get("topics", []), new_kb.get("topics", [])),
        "quiz": {
            "sections": _value_changes(old_quiz.get("sections", {}), new_quiz.get("sections", {})),
            "questions": _questions(old_quiz.get("questions", []), new_quiz.get("questions", [])),
        },
        "vocabulary": {"added": sorted(new_vocab - old_vocab), "removed": sorted(old_vocab - new_vocab)},
    }
    result["identical"] = not any(
        [
            result["settings"],
            result["lines"],
            result["preamble"],
            result["topics"],
            result["quiz"]["sections"],
            result["quiz"]["questions"],
            result["vocabulary"]["added"],
            result["vocabulary"]["removed"],
        ]
    )
    return result
