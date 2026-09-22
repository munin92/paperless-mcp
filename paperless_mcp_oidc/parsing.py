"""Shared parsing/validation utilities, ported from PaperlessMCP/Utils/*.cs."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath

# Django CharField(max_length=128), but consumer.py stores title[:127] on
# ingestion — see TitleValidation.cs for the full rationale. PATCH would
# accept 128 intact, but we validate every write path against 127 for a
# uniform contract (deliberate, see C# remarks).
MAX_TITLE_LENGTH = 127


def parse_int_array(value: str | None) -> list[int] | None:
    """Comma-separated ints, e.g. "1,2,3" -> [1, 2, 3]; None if empty/invalid-only."""
    if not value or not value.strip():
        return None
    out = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out or None


def parse_date(value: str | None) -> str | None:
    """Accepts YYYY-MM-DD (and a few common variants); returns YYYY-MM-DD or None."""
    if not value or not value.strip():
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def title_is_valid(title: str | None) -> tuple[bool, str | None]:
    """Mirrors TitleValidation.IsValid: counts Unicode code points (Python str
    already does — no UTF-16 surrogate-pair subtlety here, unlike C# char)."""
    if title is None:
        return True, None
    length = len(title)
    if length <= MAX_TITLE_LENGTH:
        return True, None
    return False, (
        f"Title is {length} characters; Paperless-ngx stores at most {MAX_TITLE_LENGTH}. "
        "Longer titles are silently truncated on upload (the model allows 128, but the "
        "consumer stores title[:127]), which would corrupt the title and the archived "
        f"file name. Shorten the title to {MAX_TITLE_LENGTH} characters or fewer and retry."
    )


def fallback_title(file_name: str | None) -> str:
    """Mirrors TitleValidation.GetFallbackTitle: Paperless computes
    Path(filename).stem server-side, so pathlib.PurePosixPath.stem IS the
    source of truth here (not an emulation of it, unlike the C# port)."""
    if not file_name:
        return ""
    trimmed = file_name.rstrip("/")
    name = trimmed.rsplit("/", 1)[-1]
    return PurePosixPath(name).stem


_SAFE_NAME_RE = re.compile(r"[\\/]")


def safe_export_filename(raw_name: str) -> str:
    """Strips any directory part so a caller-supplied "../../etc/passwd"
    collapses to "passwd" and a write stays inside the outbox dir."""
    name = raw_name.strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name or "document.pdf"


def with_document_id(file_name: str, doc_id: int) -> str:
    """Inserts the document id before the extension for export uniqueness."""
    dot = file_name.rfind(".")
    if dot <= 0:
        return f"{file_name}_{doc_id}"
    return f"{file_name[:dot]}_{doc_id}{file_name[dot:]}"
