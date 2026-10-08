"""Publish checks for a Role Play training's template (docs/ROLEPLAY.md): the rules the agent's roleplay.py
won't run without, worded for the studio. Errors block publishing; warnings don't."""

from __future__ import annotations

import re
from typing import Any

MIN_PROBE_WORDS = 4


def _issue(where: str, message: str, **at: Any) -> dict[str, Any]:
    return {"where": where, "message": message, **at}


def _words(text: str) -> int:
    return len(re.findall(r"[\w']+", text or ""))


def _filled(value: Any) -> bool:
    return bool(str(value or "").strip())


def check_roleplay(rp: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not rp:
        return [_issue("Role Play", "Fill in the Role Play template.")], warnings
    coach = rp.get("coach") or {}
    practice = rp.get("practice") or {}
    settings = rp.get("settings") or {}
    tracks = rp.get("tracks") or []
    shared = coach.get("items") or []

    if not tracks:
        errors.append(_issue("Tracks", "Add at least one track."))
    ids = [t.get("id") for t in tracks]
    if len(set(ids)) != len(ids):
        errors.append(_issue("Tracks", "Two tracks have the same id."))
    if tracks and rp.get("default_track") not in ids:
        errors.append(
            _issue("Tracks", "Choose the default track (used when a person's reason is missing or unknown).")
        )
    rubric = practice.get("rubric") or {}
    for score in ("1", "2", "3", "4", "5"):
        if not _filled(rubric.get(score)):
            errors.append(_issue("Scoring", f"Describe what a {score} means.", score=score))
        if not _filled((practice.get("score_lines") or {}).get(score)):
            warnings.append(
                _issue("Scoring", f"No line for revealing a {score}: the coach words it.", score=score)
            )
    if not 1 <= int(settings.get("unlock_score") or 0) <= 5:
        errors.append(_issue("Settings", "The score that opens the quiz must be 1 to 5."))
    if not _filled(coach.get("closing_line")):
        warnings.append(_issue("Coaching", "No closing line for the end of coaching."))

    reasons: dict[str, str] = {}
    for track in tracks:
        name = track.get("name") or track.get("id") or "?"
        where = f"Track {name}"
        for reason in [track.get("id"), track.get("name"), *(track.get("reasons") or [])]:
            key = " ".join(str(reason or "").lower().split())
            if key and reasons.get(key, name) != name:
                errors.append(
                    _issue(
                        where,
                        f'The reason "{reason}" also picks track {reasons[key]}.',
                        track=track.get("id"),
                    )
                )
            reasons.setdefault(key, name)
        steps = (track.get("framework") or {}).get("steps") or []
        if not steps:
            errors.append(
                _issue(
                    where,
                    "List the framework's steps (the practice is scored against them).",
                    track=track.get("id"),
                )
            )
        items = (
            [i for i in shared if not i.get("last")]
            + (track.get("coach_items") or [])
            + [i for i in shared if i.get("last")]
        )
        if not items:
            errors.append(_issue(where, "Add at least one coaching item.", track=track.get("id")))
        probes: set[str] = set()
        for item in items:
            label = f'{where}, coaching "{item.get("title") or item.get("id")}"'
            probe = str(item.get("probe") or "")
            if _words(probe) < MIN_PROBE_WORDS:
                errors.append(
                    _issue(
                        label, f"The question needs at least {MIN_PROBE_WORDS} words.", track=track.get("id")
                    )
                )
            key = " ".join(probe.lower().split())
            if key in probes:
                errors.append(
                    _issue(
                        label, "Two coaching questions are the same; each must differ.", track=track.get("id")
                    )
                )
            probes.add(key)
            if not _filled(item.get("right_answer")):
                errors.append(_issue(label, "Write what a good answer contains.", track=track.get("id")))
        for level in ("beginner", "stress"):
            persona = track.get(level)
            if level == "stress" and not persona:
                if settings.get("stress_enabled", True):
                    warnings.append(
                        _issue(
                            where,
                            "No tougher customer: the tougher practice won't be offered.",
                            track=track.get("id"),
                        )
                    )
                continue
            label = f"{where}, {'Beginner' if level == 'beginner' else 'tougher'} customer"
            for field, message in (
                ("name", "Name this customer."),
                ("who", "Describe who the customer is."),
                ("opening", "Write the customer's opening line."),
            ):
                if not _filled((persona or {}).get(field)):
                    errors.append(_issue(label, message, track=track.get("id"), level=level))
        quiz = track.get("quiz") or {}
        sections = quiz.get("sections") or {}
        questions = quiz.get("questions") or []
        if not questions:
            errors.append(_issue(f"{where}, quiz", "Add at least one question.", track=track.get("id")))
        needed = settings.get("quiz_pass_count")
        if needed is not None and questions and not 1 <= int(needed) <= len(questions):
            errors.append(
                _issue("Settings", f"The pass mark must be 1 to {len(questions)} questions for track {name}.")
            )
        for q in questions:
            label = f"{where}, quiz question {q.get('number')}"
            options = {k: v for k, v in (q.get("options") or {}).items() if _filled(v)}
            if sorted(options) not in (["A", "B"], ["A", "B", "C"], ["A", "B", "C", "D"]):
                errors.append(
                    _issue(
                        label,
                        "Write the options in order: A and B, then C and D if there are more.",
                        track=track.get("id"),
                        question=q.get("number"),
                    )
                )
            if q.get("correct") not in options:
                errors.append(
                    _issue(
                        label, "Choose the correct answer.", track=track.get("id"), question=q.get("number")
                    )
                )
            if not _filled(q.get("question")):
                errors.append(
                    _issue(label, "Write the question.", track=track.get("id"), question=q.get("number"))
                )
            if not _filled(q.get("explanation")):
                errors.append(
                    _issue(
                        label, "Write a short explanation.", track=track.get("id"), question=q.get("number")
                    )
                )
            if q.get("section") not in sections:
                errors.append(
                    _issue(
                        label,
                        "Put the question in a section.",
                        track=track.get("id"),
                        question=q.get("number"),
                    )
                )
        if quiz.get("draft"):
            warnings.append(
                _issue(f"{where}, quiz", f"Marked as a draft: {quiz['draft']}", track=track.get("id"))
            )
    return errors, warnings
