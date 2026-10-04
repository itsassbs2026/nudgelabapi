"""Phase 12: uploads (presigned POST, file checks, text extraction) and prepare for voice (fake Claude)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from app.config import get_settings
from app.models.content import ContentUpload
from app.schemas.training_content import TrainingContent
from app.studio import prepare, uploads
from app.studio.extract import DOCX, MARKDOWN, PDF, TEXT, Rejected, extract
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import text
from sqlalchemy.orm import Session

BUCKET = "nudgeailab"
SOURCE = (
    "Store Safety Policy\n\nKeep every exit clear at all times. The store has 3 emergency exits.\n\n"
    "If anyone is hurt, make sure they are okay, then tell your manager the same day so it can be reported."
)


# --- Extraction ------------------------------------------------------------------------------------------


def docx(paragraphs: list[str], extra_xml: str = "") -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        f'<?xml version="1.0"?>{extra_xml}<w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return out.getvalue()


def pdf(line: str) -> bytes:
    stream = f"BT /F1 18 Tf 20 100 Td ({line}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + body + b"endobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer<</Root 1 0 R/Size {len(objects) + 1}>>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_extract_each_type() -> None:
    assert (
        extract(docx(["Keep exits clear.", "Report injuries."]), DOCX, 1000)
        == "Keep exits clear.\nReport injuries."
    )
    assert "Keep exits clear" in extract(pdf("Keep exits clear at all times."), PDF, 1000)
    assert extract("\ufeffPlain   text \r\n\r\n\r\n\r\nmore".encode(), TEXT, 1000) == "Plain   text\n\nmore"
    assert extract(b"# Title\n- point", MARKDOWN, 1000) == "# Title\n- point"


@pytest.mark.parametrize(
    ("data", "content_type", "message"),
    [
        (b"%PDF-1.4 but claims to be Word", DOCX, "isn't a Word"),
        (b"PK\x03\x04 not really a zip", DOCX, "damaged"),
        (b"just text", PDF, "isn't a PDF"),
        (b"%PDF-1.4\n%%EOF", PDF, "damaged"),
        (b"text with a \x00 byte", TEXT, "isn't a plain text"),
        ("caf\u00e9".encode("latin-1"), TEXT, "isn't UTF-8"),
        (b"   \n  ", TEXT, "has no text"),
        (b"x" * 50, TEXT, "too long"),
    ],
)
def test_extract_rejects(data: bytes, content_type: str, message: str) -> None:
    with pytest.raises(Rejected, match=message):
        extract(data, content_type, 40)


def test_docx_without_a_document_part() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(Rejected, match="isn't a Word"):
        extract(out.getvalue(), DOCX, 1000)


def test_docx_entity_expansion_is_refused() -> None:
    laughs = '<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    with pytest.raises(Rejected, match="can't be read"):
        extract(docx(["&b;"], extra_xml=laughs), DOCX, 100_000)


def test_scanned_pdf_has_no_text() -> None:
    with pytest.raises(Rejected, match="Scanned documents"):
        extract(pdf(""), PDF, 1000)


# --- Upload flow (moto S3) -------------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-west-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-1"})
        yield client


@pytest.fixture
def training(client: TestClient, trainer_headers: dict[str, str]) -> str:
    r = client.post(
        "/api/v1/trainings",
        headers=trainer_headers,
        json={"training_id": "store_safety", "title": "Store Safety", "completion_type": "walkthrough"},
    )
    assert r.status_code == 201, r.text
    return "store_safety"


def start_upload(client: TestClient, headers: dict[str, str], filename: str, ct: str, size: int) -> Any:
    return client.post(
        "/api/v1/trainings/store_safety/uploads/presign",
        headers=headers,
        json={"filename": filename, "content_type": ct, "size_bytes": size},
    )


def test_upload_flow(
    client: TestClient, trainer_headers: dict[str, str], training: str, s3: Any, db_session: Session
) -> None:
    data = docx(SOURCE.split("\n\n"))
    r = start_upload(client, trainer_headers, "C:\\Users\\me\\Store Safety.docx", DOCX, len(data))
    assert r.status_code == 200, r.text
    post = r.json()
    key = post["fields"]["key"]
    assert key.startswith("training-content/alpha/pending/") and "Safety" not in key  # never the file name
    assert post["fields"]["Content-Type"] == DOCX and post["max_bytes"] == 10 * 1024 * 1024
    upload_id = post["upload_id"]

    early = client.post(f"/api/v1/uploads/{upload_id}/complete", headers=trainer_headers)
    assert early.status_code == 409 and early.json()["error"]["code"] == "not_uploaded"

    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=DOCX)  # the browser's POST
    done = client.post(f"/api/v1/uploads/{upload_id}/complete", headers=trainer_headers).json()
    assert (done["status"], done["filename"], done["size_bytes"]) == (
        "processing",
        "Store Safety.docx",
        len(data),
    )

    assert uploads.run_extract_jobs(db_session, get_settings()) == {"done": 1, "rejected": 0, "failed": 0}
    ready = client.get(f"/api/v1/uploads/{upload_id}", headers=trainer_headers).json()
    as_lines = "\n".join(SOURCE.split("\n\n"))  # one line per Word paragraph
    assert (ready["status"], ready["text_chars"]) == ("ready", len(as_lines))
    assert client.get(f"/api/v1/uploads/{upload_id}/text", headers=trainer_headers).json()["text"] == as_lines
    kept = f"training-content/alpha/uploads/{upload_id}.docx"
    assert s3.get_object(Bucket=BUCKET, Key=kept)["Body"].read() == data
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="training-content/alpha/pending/")
    listed = client.get("/api/v1/trainings/store_safety/uploads", headers=trainer_headers).json()
    assert [u["upload_id"] for u in listed] == [upload_id]


def test_a_disguised_file_is_rejected(
    client: TestClient, trainer_headers: dict[str, str], training: str, s3: Any, db_session: Session
) -> None:
    post = start_upload(client, trainer_headers, "policy.pdf", PDF, 20).json()
    s3.put_object(Bucket=BUCKET, Key=post["fields"]["key"], Body=b"MZ\x90\x00 an executable")
    client.post(f"/api/v1/uploads/{post['upload_id']}/complete", headers=trainer_headers)
    assert uploads.run_extract_jobs(db_session, get_settings())["rejected"] == 1
    got = client.get(f"/api/v1/uploads/{post['upload_id']}", headers=trainer_headers).json()
    assert (got["status"], got["error"]) == ("rejected", "This isn't a PDF file.")
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="training-content/")  # deleted
    assert client.get(f"/api/v1/uploads/{post['upload_id']}/text", headers=trainer_headers).status_code == 409


@pytest.mark.parametrize(
    ("filename", "ct", "size", "status", "code"),
    [
        ("notes.exe", TEXT, 10, 422, "unsupported_file"),
        ("notes.pdf", TEXT, 10, 422, "unsupported_file"),  # name and type must agree
        ("notes.txt", "application/zip", 10, 422, "validation_error"),
        ("notes.txt", TEXT, 11 * 1024 * 1024, 413, "file_too_large"),
        ("bad\x01name.txt", TEXT, 10, 422, "validation_error"),
    ],
)
def test_presign_refused(
    client: TestClient,
    trainer_headers: dict[str, str],
    training: str,
    s3: Any,
    filename: str,
    ct: str,
    size: int,
    status: int,
    code: str,
) -> None:
    r = start_upload(client, trainer_headers, filename, ct, size)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_only_the_uploader_completes(
    client: TestClient,
    trainer_headers: dict[str, str],
    admin_headers: dict[str, str],
    training: str,
    s3: Any,
) -> None:
    post = start_upload(client, trainer_headers, "a.txt", TEXT, 5).json()
    r = client.post(f"/api/v1/uploads/{post['upload_id']}/complete", headers=admin_headers)
    assert r.status_code == 403


def test_presign_for_unknown_training(client: TestClient, trainer_headers: dict[str, str], s3: Any) -> None:
    r = client.post(
        "/api/v1/trainings/nope_nope/uploads/presign",
        headers=trainer_headers,
        json={"filename": "a.txt", "content_type": TEXT, "size_bytes": 5},
    )
    assert r.status_code == 404


# --- Converting Claude's answer --------------------------------------------------------------------------

RAW: dict[str, Any] = {
    "opening": [{"say": "Safety is everyone's job."}],
    "topics": [
        {
            "title": "Clear exits",
            "lines": [
                {
                    "kind": "say",
                    "text": "Keep every exit clear at all times. The store has 3 emergency exits.",
                },
                {"kind": "ask", "text": "How many emergency exits does the store have?"},
                {"kind": "expected", "text": "Three."},
                {"kind": "accept", "text": "3; three exits"},
                {"kind": "key_point", "text": "There are 5 fire doors to check every morning."},
            ],
        },
        {
            "title": "Injuries",
            "lines": [
                {"kind": "say", "text": "If anyone is hurt, make sure they're okay, then tell your manager."},
                {"kind": "ask", "text": "Who do you tell when someone gets hurt?"},
                {"kind": "expected", "text": "Your manager, the same day."},
                {"kind": "key_point", "text": "Always call an ambulance first."},
                {"kind": "shout", "text": "dropped: not a known kind"},
            ],
        },
    ],
    "lines": [
        {"key": "first_message", "text": "Hi! I'm {trainer_name}. Two quick topics on store safety. Ready?"},
        {"key": "welcome_back_walkthrough", "text": "Welcome back!"},
        {"key": "already_passed", "text": "You've already done this one."},
        {"key": "feedback_question", "text": "How would you rate this training from 1 to 10?"},
        {"key": "closing", "text": "Stay safe!"},
        {"key": "completed", "text": "That's both topics."},
        {"key": "made_up_key", "text": "ignored"},
    ],
    "quiz_sections": [],
    "quiz_questions": [],
    "acknowledgment": "",
    "vocabulary": [],
}


def blank_walkthrough() -> dict[str, Any]:
    from app.studio.blank import blank_content

    return blank_content(
        title="Store Safety", completion_type="walkthrough", uses_location=False, trainer_name="Anne"
    )


def test_to_content_matches_the_agents_format() -> None:
    content, flags = prepare.to_content(RAW, blank_walkthrough())
    TrainingContent.model_validate(content)
    topics = content["knowledge_base"]["topics"]
    assert [t["title"] for t in topics] == [" Clear exits", " Injuries"]
    assert [(line["kind"], line["prefix"]) for line in topics[0]["lines"]] == [
        ("say", "Say: "),
        ("ask", "Ask: "),
        ("expected", "Expected: "),
        ("accept", "Accept: "),
        ("key_point", "Key point: "),
        ("text", ""),  # blank line between topics, as in the files
    ]
    assert (
        topics[1]["lines"][-1]["kind"] == "key_point"
    )  # unknown kind dropped, no blank line after the last topic
    training = content["training"]
    assert (training["trainer_name"], training["company"], training["completion_type"]) == (
        "Anne",
        "Prime Communications",
        "walkthrough",
    )
    assert "made_up_key" not in training["lines"]
    assert training["opening"] == [{"say": "Safety is everyone's job."}]
    assert content["quiz"] is None and content["vocabulary"] is None
    assert flags == []


def test_to_content_flags_what_is_missing() -> None:
    raw = {**RAW, "lines": [{"key": "first_message", "text": "Hi, I'm Anne!"}]}
    raw["topics"] = [{"title": "No question", "lines": [{"kind": "say", "text": "Just talk."}]}]
    _, flags = prepare.to_content(raw, blank_walkthrough())
    reasons = {f["where"]: f["reasons"][0] for f in flags}
    assert reasons["Topic 1"] == "No check-in question."
    assert reasons["Line first_message"] == "Doesn't use {trainer_name}."
    assert reasons["Line closing"] == "Not written."


def test_no_topics_is_an_error() -> None:
    with pytest.raises(prepare.PrepareError, match="No topics"):
        prepare.to_content({**RAW, "topics": []}, blank_walkthrough())


# --- The prepare job (fake Claude) -----------------------------------------------------------------------


class FakeClaude:
    """Prepare: returns RAW. Fact check: supports everything with a real quote, except as configured."""

    def __init__(self, unsupported: set[str] | None = None, bad_quote: set[str] | None = None) -> None:
        self.unsupported = unsupported or set()
        self.bad_quote = bad_quote or set()
        self.calls: list[str] = []

    def __call__(
        self, settings: Any, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        usage = {"input_tokens": 100, "output_tokens": 50}
        if "results" not in schema["properties"]:
            self.calls.append("prepare")
            assert "<source_document>" in user and "completed" in user  # the walkthrough's required lines
            return RAW, usage
        self.calls.append("fact_check")
        statements = user.split("<statements>\n", 1)[1].split("\n</statements>", 1)[0].splitlines()
        results = []
        for line in statements:
            number, statement = line.split(". ", 1)
            supported = not any(s in statement for s in self.unsupported)
            quote = "Keep every exit clear at all times." if supported else ""
            if any(s in statement for s in self.bad_quote):
                quote = "Exits may be blocked on weekends."
            results.append({"id": int(number), "supported": supported, "quote": quote, "note": ""})
        return {"results": results}, usage


@pytest.fixture
def ready_upload(training: str, db_session: Session) -> int:
    upload = ContentUpload(
        training_id="store_safety",
        s3_key="training-content/alpha/uploads/1.txt",
        original_filename="safety.txt",
        content_type=TEXT,
        size_bytes=len(SOURCE),
        status="ready",
        extracted_text=SOURCE,
    )
    db_session.add(upload)
    db_session.flush()
    return upload.id


def draft_from_upload(client: TestClient, headers: dict[str, str], upload_id: int) -> dict[str, Any]:
    r = client.post(
        "/api/v1/trainings/store_safety/versions",
        headers=headers,
        json={"source": "upload", "upload_id": upload_id, "label": "From the policy"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_prepare_end_to_end(
    client: TestClient,
    trainer_headers: dict[str, str],
    ready_upload: int,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClaude(unsupported={"ambulance"}, bad_quote={"tell your manager"})
    monkeypatch.setattr(prepare, "claude", fake)
    version = draft_from_upload(client, trainer_headers, ready_upload)
    assert version["source_upload_id"] == ready_upload
    queued = client.get(f"/api/v1/versions/{version['version_id']}/prepare", headers=trainer_headers).json()
    assert queued["status"] == "queued"

    assert prepare.run_prepare_jobs(db_session, get_settings()) == {"done": 1, "failed": 0}
    assert fake.calls == ["prepare", "fact_check"]

    content = client.get(f"/api/v1/versions/{version['version_id']}/content", headers=trainer_headers).json()
    assert content["revision"] == 2 and content["updated_by"] == "User 1"
    assert [t["title"] for t in content["content"]["knowledge_base"]["topics"]] == [
        " Clear exits",
        " Injuries",
    ]

    job = client.get(f"/api/v1/versions/{version['version_id']}/prepare", headers=trainer_headers).json()
    assert job["status"] == "done" and "content" not in job["result"]
    assert (job["result"]["topics"], job["result"]["questions"]) == (2, 0)
    flags = {(f["where"], f.get("line"), f["reasons"][0]) for f in job["result"]["flags"]}
    assert ("Topic 1", 4, "Number not in the document: 5") in flags  # "5 fire doors"
    assert ("Topic 2", 3, "Not found in the document.") in flags  # "ambulance"
    assert ("Topic 2", 0, "The supporting passage given isn't in the document; check it by hand.") in flags
    assert not any(
        f["where"] == "Topic 1" and f.get("line") == 0 for f in job["result"]["flags"]
    )  # supported
    assert job["result"]["usage"]["prepare"] == {"input_tokens": 100, "output_tokens": 50}
    assert client.get(f"/api/v1/jobs/{job['job_id']}", headers=trainer_headers).json()["status"] == "done"


def test_prepare_never_overwrites_an_edit(
    client: TestClient,
    trainer_headers: dict[str, str],
    ready_upload: int,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare, "claude", FakeClaude())
    version = draft_from_upload(client, trainer_headers, ready_upload)
    url = f"/api/v1/versions/{version['version_id']}/content"
    edited = client.get(url, headers=trainer_headers).json()["content"]
    edited["training"]["lines"]["closing"] = "Typed by a trainer while the job waited."
    assert (
        client.put(url, headers=trainer_headers, json={"revision": 1, "content": edited}).status_code == 200
    )

    assert prepare.run_prepare_jobs(db_session, get_settings()) == {"done": 0, "failed": 1}
    after = client.get(url, headers=trainer_headers).json()
    assert after["content"]["training"]["lines"]["closing"] == "Typed by a trainer while the job waited."
    job = client.get(f"/api/v1/versions/{version['version_id']}/prepare", headers=trainer_headers).json()
    assert job["status"] == "failed" and "edited while it was being prepared" in job["error"]
    stored = db_session.execute(
        text("SELECT result FROM jobs WHERE id = :i"), {"i": job["job_id"]}
    ).scalar_one()
    assert "content" in (stored if isinstance(stored, dict) else __import__("json").loads(stored))  # kept


def test_prepare_bad_ai_output_fails_cleanly(
    client: TestClient,
    trainer_headers: dict[str, str],
    ready_upload: int,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any) -> tuple[dict[str, Any], dict[str, int]]:
        return {**RAW, "topics": []}, {}

    monkeypatch.setattr(prepare, "claude", broken)
    version = draft_from_upload(client, trainer_headers, ready_upload)
    prepare.run_prepare_jobs(db_session, get_settings())
    job = client.get(f"/api/v1/versions/{version['version_id']}/prepare", headers=trainer_headers).json()
    assert (job["status"], job["error"]) == ("failed", "No topics could be made from this document.")
    content = client.get(f"/api/v1/versions/{version['version_id']}/content", headers=trainer_headers).json()
    assert content["revision"] == 1  # the blank draft is untouched


def test_prepare_rules(
    client: TestClient,
    trainer_headers: dict[str, str],
    ready_upload: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare, "claude", FakeClaude())
    version = draft_from_upload(client, trainer_headers, ready_upload)
    again = client.post(f"/api/v1/versions/{version['version_id']}/prepare", headers=trainer_headers)
    assert again.json()["error"]["code"] == "already_preparing"
    blank = client.post(
        "/api/v1/trainings/store_safety/versions", headers=trainer_headers, json={"source": "blank"}
    ).json()
    no_upload = client.post(f"/api/v1/versions/{blank['version_id']}/prepare", headers=trainer_headers)
    assert no_upload.json()["error"]["code"] == "no_upload"
    missing = client.get(f"/api/v1/versions/{blank['version_id']}/prepare", headers=trainer_headers)
    assert missing.status_code == 404
    other = client.post(
        "/api/v1/trainings/store_safety/versions", headers=trainer_headers, json={"source": "upload"}
    )
    assert other.status_code == 422  # upload_id required


def test_upload_must_be_ready_and_for_this_training(
    client: TestClient,
    trainer_headers: dict[str, str],
    ready_upload: int,
    db_session: Session,
) -> None:
    db_session.execute(
        text("UPDATE content_uploads SET status = 'processing' WHERE id = :i"), {"i": ready_upload}
    )
    r = client.post(
        "/api/v1/trainings/store_safety/versions",
        headers=trainer_headers,
        json={"source": "upload", "upload_id": ready_upload},
    )
    assert r.json()["error"]["code"] == "upload_not_ready"
    client.post("/api/v1/trainings", headers=trainer_headers, json={"training_id": "other_one", "title": "O"})
    wrong = client.post(
        "/api/v1/trainings/other_one/versions",
        headers=trainer_headers,
        json={"source": "upload", "upload_id": ready_upload},
    )
    assert wrong.json()["error"]["code"] == "wrong_upload"


def test_jobs_endpoint_hides_other_job_types(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session
) -> None:
    db_session.execute(
        text("INSERT INTO jobs (type, status, input, attempts) VALUES ('export', 'done', '{}', 0)")
    )
    job_id = db_session.execute(text("SELECT MAX(id) FROM jobs")).scalar_one()
    assert client.get(f"/api/v1/jobs/{job_id}", headers=trainer_headers).status_code == 404
