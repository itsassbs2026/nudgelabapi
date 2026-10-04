"""Checking an uploaded document and getting its text out (SPEC §12.4). Runs in the worker, never in a
request.

Each allowed type is checked by its content, not its name: a .docx must be a ZIP with word/document.xml, a PDF
must start with %PDF-, text files must be UTF-8 without NUL bytes. Word files are read with defusedxml (no
entity expansion) and with limits on the ZIP's entries and unpacked size, so a crafted file can't exhaust
memory. Scanned PDFs (images only) have no text to extract and are rejected with a clear message.
"""

from __future__ import annotations

import io
import re
import zipfile

from defusedxml import ElementTree

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"
TEXT = "text/plain"
MARKDOWN = "text/markdown"

# Content type → the extension the file name must have (lowercase).
ALLOWED: dict[str, tuple[str, ...]] = {DOCX: (".docx",), PDF: (".pdf",), TEXT: (".txt",), MARKDOWN: (".md",)}
EXTENSION = {DOCX: ".docx", PDF: ".pdf", TEXT: ".txt", MARKDOWN: ".md"}

MAX_ZIP_ENTRIES = 2_000
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 300
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class Rejected(Exception):
    """The file isn't what it says, can't be read, or has no text. The message is shown to the trainer."""


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _docx(data: bytes) -> str:
    if not data.startswith(b"PK\x03\x04"):
        raise Rejected("This isn't a Word (.docx) file.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Rejected("This Word file is damaged and can't be read.") from exc
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_UNZIPPED_BYTES:
        raise Rejected("This Word file is too large to read.")
    try:
        document = archive.read("word/document.xml")
    except KeyError as exc:
        raise Rejected("This isn't a Word (.docx) file.") from exc
    try:
        root = ElementTree.fromstring(document)
    except Exception as exc:  # defusedxml raises its own errors for forbidden constructs
        raise Rejected("This Word file can't be read.") from exc
    paragraphs = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise Rejected("This isn't a PDF file.")
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise Rejected("This PDF is password-protected. Upload a copy without a password.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise Rejected(f"This PDF has more than {MAX_PDF_PAGES} pages.")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Rejected:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise Rejected("This PDF is damaged and can't be read.") from exc


def _text(data: bytes) -> str:
    if b"\x00" in data:
        raise Rejected("This isn't a plain text file.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Rejected("This text file isn't UTF-8. Save it as UTF-8 and upload it again.") from exc


def extract(data: bytes, content_type: str, max_chars: int) -> str:
    """The document's text, tidied. A document over `max_chars` is rejected rather than cut: a training made
    from part of a document would be missing things without anyone noticing."""
    reader = {DOCX: _docx, PDF: _pdf, TEXT: _text, MARKDOWN: _text}.get(content_type)
    if reader is None:
        raise Rejected("That file type isn't supported.")
    text = _tidy(reader(data))
    if not text:
        message = "No text was found in this PDF. Scanned documents (images of text) aren't supported."
        raise Rejected(message if content_type == PDF else "This file has no text.")
    if len(text) > max_chars:
        size = f"{len(text):,} characters; the limit is {max_chars:,}"
        raise Rejected(f"This document is too long ({size}). Split it into smaller documents.")
    return text
