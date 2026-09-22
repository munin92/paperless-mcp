"""Response envelope matching barryw/PaperlessMCP's McpResponse<T> / McpErrorResponse shape."""

from __future__ import annotations

from typing import Any

from .client import new_request_id


class ErrorCodes:
    AUTH_FAILED = "AUTH_FAILED"
    NOT_ALLOWED = "NOT_ALLOWED"  # 401/403 from Paperless — extension, see README Security
    NOT_FOUND = "NOT_FOUND"
    VALIDATION = "VALIDATION"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    UNKNOWN = "UNKNOWN"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


def meta(
    base_url: str,
    *,
    page: int | None = None,
    page_size: int | None = None,
    total: int | None = None,
    next: str | None = None,  # noqa: A002 - matches the C# field name
) -> dict[str, Any]:
    return {
        "request_id": new_request_id(),
        "page": page,
        "page_size": page_size,
        "total": total,
        "next": next,
        "paperless_base_url": base_url,
    }


def ok(
    result: Any,
    base_url: str,
    *,
    page: int | None = None,
    page_size: int | None = None,
    total: int | None = None,
    next: str | None = None,  # noqa: A002
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "result": result,
        "meta": meta(base_url, page=page, page_size=page_size, total=total, next=next),
        "warnings": warnings or [],
    }


def error(code: str, message: str, base_url: str, *, details: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "details": details},
        "meta": meta(base_url),
    }


def not_allowed_error(base_url: str) -> dict[str, Any]:
    """401/403 from Paperless — never leaks response details (see README Security)."""
    return error(
        ErrorCodes.NOT_ALLOWED,
        "Paperless rejected this identity — not allowed, or not known there.",
        base_url,
    )
