"""Prepare for voice (SPEC 10.2 step 3, §12.5; Phase 12): an uploaded document → a fact-checked draft.

The worker runs it as a `prepare_for_voice` job on a draft made from an upload:
1. Claude (Bedrock, BEDROCK_PREP_MODEL) writes the training as JSON in a fixed schema (structured output),
   from the document's extracted text and the rules in prompts/prepare_for_voice.md.
2. `to_content` turns that into the agent's content document (the same format the files and the studio use)
   and it's validated like any save. AI output is untrusted: it's data to validate, never instructions.
3. Fact check, two layers: every number in a statement must appear in the source; and a second Claude call
   must quote, for each statement, the source passage that supports it, and the quote must really be in the
   source. Anything that fails either is flagged for the editor (stored in the job's result).
4. The content is saved into the draft only if nobody saved it meanwhile (the revision the job started from),
   so a trainer's edits are never overwritten. Otherwise the job fails and keeps its result.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.models.content import ContentUpload, UploadStatus
from app.models.dashboard import Job, JobStatus, JobType
from app.reference.agent_tables import training_versions, trainings
from app.schemas.training_content import TrainingContent
from app.studio.blank import required_lines
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
STALE_RUNNING = timedelta(minutes=30)
SECTION_CODES = list("ABCDEFGHIJKL")
TOPIC_KINDS = [
    "say",
    "say_exactly",
    "ask",
    "expected",
    "accept",
    "key_point",
    "key_point_exactly",
    "then_add",
]
PREFIX = {
    "say": "Say: ",
    "say_exactly": "Say exactly: ",
    "ask": "Ask: ",
    "expected": "Expected: ",
    "accept": "Accept: ",
    "key_point": "Key point: ",
    "key_point_exactly": "Key point, say exactly: ",
    "then_add": "Then add: ",
}
# Speaking time, as SPEC 10.2 estimates it (characters ÷ 15 per second), and the 30-second guideline (10.4).
SPOKEN_KINDS = {"say", "say_exactly", "ask", "key_point", "key_point_exactly", "then_add"}
CHARS_PER_SECOND = 15
MAX_TOPIC_SECONDS = 30


def speaking_seconds(lines: list[dict[str, Any]]) -> int:
    return round(sum(len(line["text"]) for line in lines if line["kind"] in SPOKEN_KINDS) / CHARS_PER_SECOND)


# Statements that carry facts and are fact-checked. Questions and accepted answers are judged by people.
CHECKED_KINDS = {"say", "say_exactly", "expected", "key_point", "key_point_exactly", "then_add"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- What Claude returns ---------------------------------------------------------------------------------


def output_schema(line_keys: list[str]) -> dict[str, Any]:
    def obj(props: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    string = {"type": "string"}
    return obj(
        {
            "opening": {"type": "array", "items": obj({"say": string})},
            "topics": {
                "type": "array",
                "items": obj(
                    {
                        "title": string,
                        "lines": {
                            "type": "array",
                            "items": obj({"kind": {"type": "string", "enum": TOPIC_KINDS}, "text": string}),
                        },
                    }
                ),
            },
            "lines": {
                "type": "array",
                "items": obj({"key": {"type": "string", "enum": line_keys}, "text": string}),
            },
            "quiz_sections": {
                "type": "array",
                "items": obj(
                    {
                        "code": {"type": "string", "enum": SECTION_CODES},
                        "name": string,
                        "topics": {"type": "array", "items": {"type": "integer"}},
                    }
                ),
            },
            "quiz_questions": {
                "type": "array",
                "items": obj(
                    {
                        "section": {"type": "string", "enum": SECTION_CODES},
                        "question": string,
                        "option_a": string,
                        "option_b": string,
                        "option_c": string,
                        "correct": {"type": "string", "enum": ["A", "B", "C"]},
                        "explanation": string,
                    }
                ),
            },
            "acknowledgment": string,
            "vocabulary": {"type": "array", "items": string},
        }
    )


FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "supported": {"type": "boolean"},
                    "quote": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "supported", "quote", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


# --- Calling Claude --------------------------------------------------------------------------------------

Caller = Callable[[Settings, str, str, dict[str, Any]], tuple[dict[str, Any], dict[str, int]]]


SUBMIT_INSTRUCTION = "When you're done, call the submit tool once with the whole result. Write nothing else."


def call_claude(
    settings: Settings, system: str, user: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int]]:
    """One request to Claude on Bedrock: (the object it submitted, token usage).

    The result comes back as the input of a `submit` tool whose schema is `schema`. Bedrock rejects
    `output_config.format` and `strict` tools for this model, and Sonnet 5.5 rejects a forced tool choice, so
    the tool is offered with the default choice and the instructions ask for it. The input isn't guaranteed to
    match the schema: everything that reads it checks it (to_content, TrainingContent, the fact check).
    """
    from anthropic import AnthropicBedrock

    client = AnthropicBedrock(
        aws_region=settings.bedrock_region, timeout=settings.prep_timeout_seconds, max_retries=2
    )
    tool = {"name": "submit", "description": "Submit the finished result, once.", "input_schema": schema}
    with client.messages.stream(
        model=settings.bedrock_prep_model,
        max_tokens=settings.prep_max_tokens,
        system=system + "\n\n" + SUBMIT_INSTRUCTION,
        messages=[{"role": "user", "content": user}],
        tools=[tool],  # type: ignore[list-item]
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "max_tokens":
        raise PrepareError("The document is too long to prepare in one go. Split it into smaller documents.")
    if message.stop_reason == "refusal":
        raise PrepareError("The AI declined to prepare this document.")
    submitted = next((b.input for b in message.content if b.type == "tool_use" and b.name == "submit"), None)
    if not isinstance(submitted, dict):
        raise PrepareError("The AI didn't return a result. Try again.")
    usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens}
    return submitted, usage


# The worker uses this; tests replace it with a fake.
claude: Caller = call_claude


class PrepareError(Exception):
    """Shown to the trainer as the job's error."""


# --- Turning Claude's answer into the agent's content format ---------------------------------------------


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split()) if isinstance(text, str | int | float) else ""


def _items(value: Any) -> list[dict[str, Any]]:
    """The dicts in a list from Claude's answer; anything else is ignored (it isn't schema-checked)."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ints(value: Any) -> set[int]:
    out = set()
    for item in value if isinstance(value, list) else []:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def to_content(raw: dict[str, Any], draft: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(content document, structural flags). Settings that aren't Claude's to decide (trainer name, company,
    completion type, location, vocabulary name) come from the draft."""
    settings = dict(draft.get("training", {}))
    completion_type = settings.get("completion_type", "quiz")
    flags: list[dict[str, Any]] = []

    topics: list[dict[str, Any]] = []
    for i, topic in enumerate(_items(raw.get("topics")), start=1):
        lines = [
            {"kind": line["kind"], "prefix": PREFIX[line["kind"]], "text": _clean(line.get("text"))}
            for line in _items(topic.get("lines"))
            if line.get("kind") in PREFIX and _clean(line.get("text"))
        ]
        if not any(line["kind"] == "ask" for line in lines):
            flags.append({"where": f"Topic {i}", "topic": i, "reasons": ["No check-in question."]})
        seconds = speaking_seconds(lines)
        if seconds > MAX_TOPIC_SECONDS:
            reason = f"About {seconds} seconds of speech; aim for {MAX_TOPIC_SECONDS} or less (split it)."
            flags.append({"where": f"Topic {i}", "topic": i, "reasons": [reason]})
        topics.append({"number": i, "title": " " + _clean(topic.get("title")), "lines": lines})
    for topic in topics[:-1]:  # a blank line between topics, as in the files
        topic["lines"].append({"kind": "text", "prefix": "", "text": ""})
    if not topics:
        raise PrepareError("No topics could be made from this document.")

    wanted = required_lines(completion_type, bool(settings.get("uses_location")))
    written = {
        item["key"]: _clean(item.get("text"))
        for item in _items(raw.get("lines"))
        if item.get("key") in wanted
    }
    settings["lines"] = {key: written.get(key, "") for key in wanted}
    for key in wanted:
        if not settings["lines"][key]:
            flags.append({"where": f"Line {key}", "line_key": key, "reasons": ["Not written."]})
    if "{trainer_name}" not in settings["lines"].get("first_message", ""):
        flags.append(
            {
                "where": "Line first_message",
                "line_key": "first_message",
                "reasons": ["Doesn't use {trainer_name}."],
            }
        )
    opening = [
        {"say": _clean(step.get("say"))} for step in _items(raw.get("opening")) if _clean(step.get("say"))
    ]
    if opening:
        settings["opening"] = opening
    else:
        settings.pop("opening", None)
    if completion_type == "acknowledgment":
        settings["acknowledgment"] = _clean(raw.get("acknowledgment"))

    quiz = None
    if completion_type == "quiz":
        numbers = {t["number"] for t in topics}
        sections = {}
        for section in _items(raw.get("quiz_sections")):
            code = section.get("code")
            if code in SECTION_CODES and code not in sections:
                sections[code] = {
                    "name": _clean(section.get("name")),
                    "topics": sorted(_ints(section.get("topics")) & numbers),
                }
        questions: list[dict[str, Any]] = []
        for q in _items(raw.get("quiz_questions")):
            if q.get("section") not in sections or q.get("correct") not in ("A", "B", "C"):
                dropped = "A question was dropped: no valid section or answer."
                flags.append({"where": "Quiz", "reasons": [dropped]})
                continue
            questions.append(
                {
                    "number": len(questions) + 1,
                    "section": q["section"],
                    "question": _clean(q.get("question")),
                    "options": {
                        "A": _clean(q.get("option_a")),
                        "B": _clean(q.get("option_b")),
                        "C": _clean(q.get("option_c")),
                    },  # fmt: skip
                    "correct": q.get("correct"),
                    "explanation": _clean(q.get("explanation")),
                }
            )
        if not questions:
            flags.append({"where": "Quiz", "reasons": ["No quiz questions."]})
        quiz = {"sections": sections, "questions": questions}

    if settings.get("uses_location"):
        flags.append(
            {
                "where": "Training",
                "reasons": ["Location-specific lines aren't generated. Add them by hand if needed."],
            }
        )
    terms = raw.get("vocabulary")
    vocabulary = [_clean(v) for v in terms if _clean(v)] if isinstance(terms, list) else []
    content = {
        "format": 1,
        "training": settings,
        "knowledge_base": {"preamble": [], "topics": topics, "ends_with_newline": True},
        "quiz": quiz,
        "vocabulary": vocabulary or None,
    }
    return content, flags


# --- Fact check ------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty"
)
_TENS = {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORDS = {word: value for value, word in enumerate(_UNITS.split())} | _TENS | {"hundred": 100}


def _numbers(text: str) -> set[str]:
    found = {n.replace(",", "").rstrip(".").removesuffix(".0") for n in _NUMBER.findall(text)}
    found |= {str(v) for w, v in _WORDS.items() if re.search(rf"\b{w}\b", text, re.IGNORECASE)}
    return found


_PUNCTUATION: dict[str, str | int | None] = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
}  # fmt: skip


def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().translate(str.maketrans(_PUNCTUATION))
    return " ".join(re.sub(r"[^\w%$'\-.]+", " ", text).split())


def statements(content: dict[str, Any]) -> list[dict[str, Any]]:
    """The fact-bearing statements of a training, with where each one is."""
    out: list[dict[str, Any]] = []
    for i, step in enumerate(content["training"].get("opening", []), start=1):
        out.append({"where": f"Opening {i}", "opening": i, "text": step["say"]})
    for topic in content["knowledge_base"]["topics"]:
        for j, line in enumerate(topic["lines"]):
            if line["kind"] in CHECKED_KINDS:
                out.append({"where": f"Topic {topic['number']}", "topic": topic["number"], "line": j,
                            "kind": line["kind"], "text": line["text"]})  # fmt: skip
    for q in (content.get("quiz") or {}).get("questions", []):
        answer = q["options"].get(q["correct"], "")
        out.append({"where": f"Quiz question {q['number']}", "question": q["number"],
                    "text": f"{q['question']} Answer: {answer}. {q['explanation']}"})  # fmt: skip
    if content["training"].get("acknowledgment"):
        out.append({"where": "Acknowledgment", "text": content["training"]["acknowledgment"]})
    return out


def number_flags(items: list[dict[str, Any]], source: str) -> dict[int, list[str]]:
    in_source = _numbers(source)
    flags: dict[int, list[str]] = {}
    for i, item in enumerate(items):
        missing = sorted(_numbers_in_digits(item["text"]) - in_source)
        if missing:
            flags[i] = [f"Number not in the document: {', '.join(missing)}"]
    return flags


def _numbers_in_digits(text: str) -> set[str]:
    """Only numbers written as digits are checked: "number one" in a spoken line isn't a fact to verify."""
    return {n.replace(",", "").rstrip(".").removesuffix(".0") for n in _NUMBER.findall(text)}


def quote_flags(
    items: list[dict[str, Any]], results: list[dict[str, Any]], source: str
) -> dict[int, list[str]]:
    normal_source = _normal(source)
    by_id = {int(r["id"]): r for r in results if isinstance(r.get("id"), int)}
    flags: dict[int, list[str]] = {}
    for i in range(len(items)):
        result = by_id.get(i + 1)
        if result is None:
            flags[i] = ["Not checked."]
        elif not result.get("supported"):
            flags[i] = [f"Not found in the document. {_clean(result.get('note'))}".strip()]
        elif result.get("quote") and _normal(result["quote"]) not in normal_source:
            flags[i] = ["The supporting passage given isn't in the document; check it by hand."]
    return flags


def fact_check(
    content: dict[str, Any], source: str, settings: Settings
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = statements(content)
    if not items:
        return [], {}
    numbered = "\n".join(f"{i}. {item['text']}" for i, item in enumerate(items, start=1))
    user = f"<source_document>\n{source}\n</source_document>\n\n<statements>\n{numbered}\n</statements>"
    raw, usage = claude(settings, (PROMPTS / "fact_check.md").read_text(encoding="utf-8"), user, FACT_SCHEMA)
    reasons: dict[int, list[str]] = {}
    for found in (number_flags(items, source), quote_flags(items, _items(raw.get("results")), source)):
        for i, why in found.items():
            reasons.setdefault(i, []).extend(why)
    flags = [
        {**{k: v for k, v in items[i].items() if k != "text"}, "text": items[i]["text"], "reasons": why}
        for i, why in sorted(reasons.items())
    ]
    return flags, usage


# --- Building the request --------------------------------------------------------------------------------


def request_text(source: str, title: str, completion_type: str, line_keys: list[str]) -> str:
    how = {
        "quiz": "It ends with a multiple-choice quiz; the trainee must get every question right.",
        "walkthrough": "There's no quiz: the trainee completes it by going through every topic.",
        "acknowledgment": "After the topics, the trainee confirms the acknowledgment statement out loud.",
    }[completion_type]
    return (
        f"Training title: {title}\nCompletion type: {completion_type}. {how}\n"
        f"Scripted lines to write: {', '.join(line_keys)}\n\n"
        f"<source_document>\n{source}\n</source_document>\n\n"
        "Write the training from this document."
    )


def prepare(source: str, draft: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The whole pipeline, without the database: content, flags, usage."""
    training = draft.get("training", {})
    completion_type = training.get("completion_type", "quiz")
    keys = list(required_lines(completion_type, bool(training.get("uses_location"))))
    raw, usage = claude(
        settings,
        (PROMPTS / "prepare_for_voice.md").read_text(encoding="utf-8"),
        request_text(source, training.get("title", ""), completion_type, keys),
        output_schema(keys),
    )
    content, structure_flags = to_content(raw, draft)
    try:
        TrainingContent.model_validate(content)
    except ValidationError as exc:
        logger.warning("prepare_invalid_content", errors=exc.errors()[:5])
        raise PrepareError("The AI's draft wasn't in a usable shape. Try again.") from exc
    flags, check_usage = fact_check(content, source, settings)
    return {
        "content": content,
        "flags": structure_flags + flags,
        "usage": {"prepare": usage, "fact_check": check_usage},
        "topics": len(content["knowledge_base"]["topics"]),
        "questions": len((content.get("quiz") or {}).get("questions", [])),
    }


# --- Jobs ------------------------------------------------------------------------------------------------


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


def queue(db: Session, current: CurrentUser, version_id: int) -> Job:
    version = db.execute(
        select(training_versions).where(training_versions.c.version_id == version_id)
    ).first()
    if version is None:
        raise ApiError(404, "not_found", "Version not found.")
    if version.status != "draft":
        raise ApiError(409, "not_a_draft", "Only a draft can be prepared.")
    if version.source_upload_id is None:
        raise ApiError(409, "no_upload", "This draft wasn't made from an uploaded document.")
    upload = db.get(ContentUpload, version.source_upload_id)
    if upload is None or upload.status != UploadStatus.READY.value:
        raise ApiError(409, "upload_not_ready", "The document's text isn't ready yet.")
    busy = db.scalars(
        select(Job.id).where(
            Job.type == JobType.PREPARE_FOR_VOICE.value,
            Job.version_id == version_id,
            Job.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)),
        )
    ).first()
    if busy:
        raise ApiError(409, "already_preparing", "This draft is already being prepared.")
    job = Job(
        type=JobType.PREPARE_FOR_VOICE.value,
        training_id=version.training_id,
        version_id=version_id,
        status=JobStatus.QUEUED.value,
        input={"upload_id": upload.id, "base_revision": version.revision},
        created_by=current.user.id,
    )
    db.add(job)
    db.commit()
    return job


def _run_one(db: Session, settings: Settings, job: Job) -> dict[str, Any]:
    version = db.execute(
        select(training_versions).where(training_versions.c.version_id == job.version_id)
    ).first()
    upload = db.get(ContentUpload, int(job.input["upload_id"]))
    if version is None or upload is None or not upload.extracted_text:
        raise PrepareError("The draft or its document is gone.")
    training = db.execute(select(trainings).where(trainings.c.training_id == version.training_id)).first()
    draft = _json(version.content) or {}
    draft.setdefault("training", {}).setdefault("title", training.title if training else "")
    result = prepare(upload.extracted_text, draft, settings)
    v = training_versions.c
    saved = db.execute(
        update(training_versions)
        .where(
            v.version_id == job.version_id, v.revision == int(job.input["base_revision"]), v.status == "draft"
        )
        .values(
            content=result["content"], revision=v.revision + 1, updated_at=_now(), updated_by=job.created_by
        )
    )
    if saved.rowcount != 1:  # type: ignore[attr-defined]
        job.result = result
        raise PrepareError("The draft was edited while it was being prepared, so the result wasn't saved.")
    return {k: v for k, v in result.items() if k != "content"}


def run_prepare_jobs(db: Session, settings: Settings) -> dict[str, int]:
    """Worker job: prepare queued drafts, one at a time (each takes a few minutes)."""
    now = _now()
    for job in db.scalars(
        select(Job).where(
            Job.type == JobType.PREPARE_FOR_VOICE.value,
            Job.status == JobStatus.RUNNING.value,
            Job.started_at < now - STALE_RUNNING,
        )
    ).all():
        job.status, job.error, job.finished_at = JobStatus.FAILED.value, "Interrupted; please try again.", now
    db.commit()
    queued = db.scalars(
        select(Job)
        .where(Job.type == JobType.PREPARE_FOR_VOICE.value, Job.status == JobStatus.QUEUED.value)
        .order_by(Job.created_at, Job.id)
        .limit(1)
    ).first()
    if queued is None:
        return {"done": 0, "failed": 0}
    job = queued
    job.status, job.started_at, job.attempts = JobStatus.RUNNING.value, _now(), job.attempts + 1
    db.commit()
    try:
        job.result = _run_one(db, settings, job)
        job.status = JobStatus.DONE.value
    except PrepareError as exc:
        kept = job.result
        db.rollback()
        job.status, job.error, job.result = JobStatus.FAILED.value, str(exc)[:500], kept
    except Exception:
        db.rollback()
        logger.exception("prepare_failed", job_id=job.id)
        job.status, job.error = JobStatus.FAILED.value, "Preparing this draft failed. Try again."
    job.finished_at = _now()
    db.commit()
    return {
        "done": int(job.status == JobStatus.DONE.value),
        "failed": int(job.status == JobStatus.FAILED.value),
    }


def job_view(job: Job, include_content: bool = False) -> dict[str, Any]:
    result = dict(job.result or {})
    if not include_content:
        result.pop("content", None)
    return {
        "job_id": job.id,
        "type": job.type,
        "status": job.status,
        "training_id": job.training_id,
        "version_id": job.version_id,
        "error": job.error,
        "result": result or None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def latest_for_version(db: Session, version_id: int) -> dict[str, Any] | None:
    job = db.scalars(
        select(Job)
        .where(Job.type == JobType.PREPARE_FOR_VOICE.value, Job.version_id == version_id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    ).first()
    return job_view(job) if job else None
