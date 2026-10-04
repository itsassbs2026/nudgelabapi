"""Checking a training before it can be published (SPEC 10.4).

Errors block publishing (Phase 16 enforces them); warnings don't. The studio shows both after each save. Every
issue says where it is (topic number, line key, quiz question) so the editor can point at it.

Errors:
- at least one topic; every topic has an Ask of at least 4 words; Ask lines are all different (the agent's
  topic tracker recognizes topics by them)
- quiz trainings: at least one question; each with two or three options (A, B, C), a correct letter among
  them and an explanation (Q4 comp has true/false questions with two); every
  section maps to topics that exist, and every question's section exists
- acknowledgment trainings: a statement of 10–300 characters
- every scripted line the completion type needs, written (not empty)
- vocabulary terms Amazon Transcribe accepts: letters with periods, hyphens or apostrophes (P.P.V.G.A.,
  Protect-Advantage), no spaces or digits, at most 256 characters, at most 500 terms (Phase 16 publishes them)
Warnings:
- a topic over 30 seconds of speech; an Ask without an Expected answer or with no Accept line
- the welcome line doesn't use {trainer_name}, or a line says the trainer's name instead of {trainer_name}
- no completion key (passes won't reach Wanaka or Portal)
"""

from __future__ import annotations

import re
from typing import Any, cast

from app.schemas.training_content import CompletionType
from app.studio.blank import REQUIRED_LINES, required_lines
from app.studio.prepare import MAX_TOPIC_SECONDS, speaking_seconds

MIN_ASK_WORDS = 4
VOCAB_TERM = re.compile(r"^[A-Za-z][A-Za-z.'\-]{0,255}$")
MAX_VOCAB_TERMS = 500
ACK_MIN, ACK_MAX = 10, 300


def _issue(where: str, message: str, **at: Any) -> dict[str, Any]:
    return {"where": where, "message": message, **at}


def _words(text: str) -> int:
    return len(re.findall(r"[\w']+", text))


def validate(content: dict[str, Any], *, completion_key: str | None) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    training = content.get("training", {})
    found = training.get("completion_type")
    completion_type: CompletionType = cast(CompletionType, found) if found in REQUIRED_LINES else "quiz"
    topics = content.get("knowledge_base", {}).get("topics", [])

    if not topics:
        errors.append(_issue("Topics", "Add at least one topic."))
    asks: dict[str, int] = {}
    for topic in topics:
        number = topic["number"]
        where = f"Topic {number}"
        lines = topic.get("lines", [])
        topic_asks = [line for line in lines if line["kind"] == "ask"]
        if not topic_asks:
            errors.append(_issue(where, "Add a check-in question (Ask).", topic=number))
        for line in topic_asks:
            text = line["text"].strip()
            if _words(text) < MIN_ASK_WORDS:
                errors.append(
                    _issue(where, f"The question needs at least {MIN_ASK_WORDS} words.", topic=number)
                )
            key = " ".join(text.lower().split())
            if key in asks:
                errors.append(
                    _issue(
                        where, f"Same question as topic {asks[key]}; each question must differ.", topic=number
                    )
                )
            else:
                asks[key] = number
        if topic_asks and not any(line["kind"] == "expected" for line in lines):
            warnings.append(
                _issue(where, "No Expected answer: the trainer can't judge the reply.", topic=number)
            )
        if topic_asks and not any(line["kind"] == "accept" for line in lines):
            warnings.append(_issue(where, "No Accept line: only the Expected answer counts.", topic=number))
        seconds = speaking_seconds(lines)
        if seconds > MAX_TOPIC_SECONDS:
            warnings.append(
                _issue(
                    where,
                    f"About {seconds} seconds of speech; aim for {MAX_TOPIC_SECONDS} or less.",
                    topic=number,
                )
            )

    numbers = {topic["number"] for topic in topics}
    if completion_type == "quiz":
        quiz = content.get("quiz") or {}
        sections = quiz.get("sections", {})
        questions = quiz.get("questions", [])
        if not questions:
            errors.append(_issue("Quiz", "Add at least one question."))
        for code, section in sections.items():
            missing = sorted(set(section.get("topics", [])) - numbers)
            if missing:
                where = f"Quiz section {code}"
                errors.append(_issue(where, f"Refers to topics that don't exist: {missing}.", section=code))
            if not section.get("topics"):
                errors.append(
                    _issue(f"Quiz section {code}", "Pick the topics this section covers.", section=code)
                )
        for q in questions:
            where = f"Quiz question {q['number']}"
            bodies = list(q["variants"].values()) if q.get("variants") else [q]
            for body in bodies:
                options = body.get("options") or {}
                written = sorted(k for k, v in options.items() if str(v).strip())
                if written not in (["A", "B"], ["A", "B", "C"]):
                    message = "Write the options: A and B, and C if there are three."
                    errors.append(_issue(where, message, question=q["number"]))
                if body.get("correct") not in written:
                    errors.append(_issue(where, "Choose the correct answer.", question=q["number"]))
                if not str(body.get("explanation") or "").strip():
                    errors.append(_issue(where, "Write a short explanation.", question=q["number"]))
                if not str(body.get("question") or "").strip():
                    errors.append(_issue(where, "Write the question.", question=q["number"]))
            if q.get("section") not in sections:
                errors.append(_issue(where, "Put the question in a section.", question=q["number"]))

    if completion_type == "acknowledgment":
        statement = str(training.get("acknowledgment") or "").strip()
        if not ACK_MIN <= len(statement) <= ACK_MAX:
            message = f"Write the statement the trainee confirms ({ACK_MIN}–{ACK_MAX} characters)."
            errors.append(_issue("Acknowledgment", message))

    lines = training.get("lines", {})
    for key in required_lines(completion_type, bool(training.get("uses_location"))):
        if not str(lines.get(key) or "").strip():
            errors.append(_issue(f"Line {key}", "Write this line.", line_key=key))
    if lines.get("first_message") and "{trainer_name}" not in lines["first_message"]:
        warnings.append(
            _issue(
                "Line first_message",
                "Use {trainer_name} so each trainee hears their trainer's name.",
                line_key="first_message",
            )  # fmt: skip
        )
    name = str(training.get("trainer_name") or "").strip()
    if name:
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        for key, text in lines.items():
            if pattern.search(str(text)):
                warnings.append(
                    _issue(f"Line {key}", f'Says "{name}"; use {{trainer_name}} instead.', line_key=key)
                )
    vocabulary = content.get("vocabulary") or []
    if len(vocabulary) > MAX_VOCAB_TERMS:
        errors.append(_issue("Vocabulary", f"At most {MAX_VOCAB_TERMS} terms."))
    for term in vocabulary:
        if not VOCAB_TERM.match(str(term)):
            message = f'"{term}": only letters with periods, hyphens or apostrophes (P.P.V.G.A., Pro-Plan)'
            errors.append(_issue("Vocabulary", message, term=str(term)))
    if not completion_key:
        warnings.append(_issue("Settings", "No completion key: passes won't be copied to Wanaka or Portal."))
    return {"errors": errors, "warnings": warnings}
