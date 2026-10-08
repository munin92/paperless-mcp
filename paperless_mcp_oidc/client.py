"""Async HTTP client for Paperless-ngx, scoped to exactly one identity.

Every client speaks to Paperless with `Authorization: Token <drf-token>` —
either the shared `PAPERLESS_API_TOKEN` (AUTH_MODE=token) or a per-person DRF
token obtained through the Keycloak token exchange (AUTH_MODE=oidc, see
identity.py). This client never sends a remote-user header: it does not know,
and does not need to know, which auth mode produced its token.

`on_unauthorized`, when given, is an async callable that returns a fresh
token; it is used to retry a request exactly once after a 401/403 (see
identity.py's token-exchange retry), not to implement retries in general.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

# Paperless-ngx 3.x supports REST API versions 9 and 10; 10 is current default.
API_ACCEPT_HEADER = "application/json; version=10"
DEFAULT_OUTBOX_DIR = "/home/mcp/outbox"
DEFAULT_MAX_PAGE_SIZE = 100

# base64 inflates ~4/3 and the whole tool response must fit the model's
# tool-output budget, so inline downloads are capped small; anything larger
# must go through paperless_documents_export_to_outbox.
MAX_INLINE_BASE64_BYTES = 12 * 1024


class PaperlessError(RuntimeError):
    """A Paperless API call failed in a way the caller should see."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PaperlessNotAllowed(PaperlessError):
    """Paperless rejected this identity (401/403) — not allowed, or not known there."""

    def __init__(self, status_code: int):
        super().__init__(
            "Paperless rejected this identity — not allowed, or not known there.",
            status_code=status_code,
        )


class PaperlessClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        host_header: str = "",
        timeout: float = 15,
        outbox_dir: str = DEFAULT_OUTBOX_DIR,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        confine_paths_to_outbox: bool = False,
        on_unauthorized: Callable[[], Awaitable[str]] | None = None,
    ):
        if not token:
            raise ValueError("PaperlessClient needs a token")

        self._base_url = base_url.rstrip("/")
        self._headers = {"Accept": API_ACCEPT_HEADER, "Authorization": f"Token {token}"}
        if host_header:
            self._headers["Host"] = host_header

        self._timeout = timeout
        self.outbox_dir = outbox_dir
        # Multi-user (oidc mode): file tools may only touch the caller's own
        # outbox, never the container — set explicitly by server.get_client().
        self.confine_paths_to_outbox = confine_paths_to_outbox
        self.max_page_size = max_page_size if max_page_size > 0 else DEFAULT_MAX_PAGE_SIZE
        self._on_unauthorized = on_unauthorized

    @property
    def base_url(self) -> str:
        return self._base_url

    def effective_page_size(self, requested: int | None = None) -> int:
        """Normalizes a requested page size to the configured positive upper bound."""
        cap = self.max_page_size
        value = requested if requested is not None else cap
        return max(1, min(value, cap))

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _raise_for_identity(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise PaperlessNotAllowed(resp.status_code)

    async def _send(
        self, method: str, path: str, *, params: dict | None = None, json_body: Any = None
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            return await c.request(
                method, self._url(path), params=params, json=json_body, headers=self._headers
            )

    async def _retry_once_on_unauthorized(
        self, resp: httpx.Response, retry: Callable[[], Awaitable[httpx.Response]]
    ) -> httpx.Response:
        """On a 401/403, if an on_unauthorized refresh hook is configured, get a
        fresh token and retry the same request exactly once. A second 401/403
        is reported as-is — the caller (client.py's _raise_for_identity /
        upload_document) turns that into PaperlessNotAllowed."""
        if resp.status_code not in (401, 403) or self._on_unauthorized is None:
            return resp
        new_token = await self._on_unauthorized()
        self._headers["Authorization"] = f"Token {new_token}"
        return await retry()

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json_body: Any = None
    ) -> httpx.Response:
        resp = await self._send(method, path, params=params, json_body=json_body)
        resp = await self._retry_once_on_unauthorized(
            resp, lambda: self._send(method, path, params=params, json_body=json_body)
        )
        self._raise_for_identity(resp)
        return resp

    # --- generic JSON helpers ------------------------------------------

    async def get(self, path: str, params: dict | None = None) -> Any:
        resp = await self._request("GET", path, params=params)
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code}: {resp.reason_phrase}", resp.status_code, resp.text
            )
        return resp.json() if resp.content else None

    async def get_or_none(self, path: str) -> Any:
        """Like get(), but None on any failure — mirrors barryw/PaperlessMCP's
        GetAsync<T>, which the "get by id" tools treat as NOT_FOUND regardless
        of the underlying reason. 401/403 still raise: unlike a real 404, an
        identity rejection must be reported as such, not folded into
        NOT_FOUND (see README Security section)."""
        try:
            return await self.get(path, None)
        except PaperlessNotAllowed:
            raise
        except PaperlessError:
            return None

    async def get_paginated(self, path: str, params: dict | None = None) -> dict:
        data = await self.get(path, params=params)
        return data or {"count": 0, "next": None, "previous": None, "results": []}

    async def post(self, path: str, json_body: dict | list | None = None) -> Any:
        resp = await self._request("POST", path, json_body=json_body)
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code}: {resp.reason_phrase}", resp.status_code, resp.text
            )
        return resp.json() if resp.content else None

    async def patch(self, path: str, json_body: dict) -> Any:
        resp = await self._request("PATCH", path, json_body=json_body)
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code}: {resp.reason_phrase}", resp.status_code, resp.text
            )
        return resp.json() if resp.content else None

    async def delete(self, path: str) -> bool:
        resp = await self._request("DELETE", path)
        return resp.status_code < 400 or resp.status_code == 204

    # --- documents: search -----------------------------------------------

    async def search_documents(self, params: list[tuple[str, str]]) -> dict:
        return await self.get_paginated("/api/documents/", params=params)

    # --- documents: upload -------------------------------------------------

    async def _post_document(self, parts: list[tuple[str, tuple[str | None, Any]]]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=max(self._timeout, 300)) as c:
            return await c.post(
                self._url("/api/documents/post_document/"),
                files=parts,
                headers=self._headers,
            )

    async def upload_document(
        self,
        file_content: bytes,
        file_name: str,
        *,
        title: str | None = None,
        correspondent: int | None = None,
        document_type: int | None = None,
        storage_path: int | None = None,
        tags: list[int] | None = None,
        archive_serial_number: int | None = None,
        created: str | None = None,
    ) -> str | None:
        # All fields (including repeated "tags") go through `files=` as
        # (name, (None, value)) tuples rather than `data=`: httpx/respx mishandle
        # a `data=` list of tuples alongside `files=` (observed as a spurious
        # "sync request with an AsyncClient" error), and `data=` as a plain dict
        # can't carry repeated keys for multiple tags anyway.
        parts: list[tuple[str, tuple[str | None, Any]]] = [("document", (file_name, file_content))]
        if title:
            parts.append(("title", (None, title)))
        if correspondent is not None:
            parts.append(("correspondent", (None, str(correspondent))))
        if document_type is not None:
            parts.append(("document_type", (None, str(document_type))))
        if storage_path is not None:
            parts.append(("storage_path", (None, str(storage_path))))
        if archive_serial_number is not None:
            parts.append(("archive_serial_number", (None, str(archive_serial_number))))
        if created:
            parts.append(("created", (None, created)))
        for t in tags or []:
            parts.append(("tags", (None, str(t))))

        resp = await self._post_document(parts)
        resp = await self._retry_once_on_unauthorized(resp, lambda: self._post_document(parts))
        self._raise_for_identity(resp)
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code}: {resp.reason_phrase}", resp.status_code, resp.text
            )
        return resp.text.strip('"')

    async def upload_document_from_path(
        self, file_path: Path, **metadata: Any
    ) -> str | None:
        content = file_path.read_bytes()
        return await self.upload_document(content, file_path.name, **metadata)

    # --- documents: download / outbox -------------------------------------

    async def open_document_file(
        self, doc_id: int, *, original: bool = False
    ) -> tuple[bytes, str | None, str | None]:
        """Returns (content, content_type, content_disposition_filename)."""
        params = {"original": "true"} if original else None
        resp = await self._request("GET", f"/api/documents/{doc_id}/download/", params=params)
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code} downloading document {doc_id}", resp.status_code
            )
        content_type = resp.headers.get("content-type")
        disposition = resp.headers.get("content-disposition")
        suggested_name = None
        if disposition and "filename=" in disposition:
            suggested_name = disposition.split("filename=")[-1].strip('"; ')
        return resp.content, content_type, suggested_name

    async def get_thumbnail(self, doc_id: int) -> tuple[bytes, str | None]:
        """Returns (content, content_type) of the document's thumbnail."""
        resp = await self._request("GET", f"/api/documents/{doc_id}/thumb/")
        if resp.status_code >= 400:
            raise PaperlessError(
                f"HTTP {resp.status_code} fetching thumbnail of document {doc_id}", resp.status_code
            )
        return resp.content, resp.headers.get("content-type")

    # --- bulk operations ---------------------------------------------------

    async def bulk_edit_documents(
        self, document_ids: list[int], method: str, parameters: dict | None = None
    ) -> None:
        body: dict[str, Any] = {"documents": document_ids, "method": method}
        if parameters is not None:
            body["parameters"] = parameters
        await self.post("/api/documents/bulk_edit/", body)

    async def bulk_edit_objects(
        self, object_ids: list[int], object_type: str, operation: str, parameters: dict | None = None
    ) -> None:
        body: dict[str, Any] = {
            "objects": object_ids,
            "object_type": object_type,
            "operation": operation,
        }
        if parameters is not None:
            body["parameters"] = parameters
        await self.post("/api/bulk_edit_objects/", body)

    # --- health --------------------------------------------------------

    async def status(self) -> dict | None:
        return await self.get_or_none("/api/status/")


def new_request_id() -> str:
    return str(uuid.uuid4())


def parse_next_page(next_url: str | None) -> int | None:
    if not next_url:
        return None
    qs = parse_qs(urlparse(next_url).query)
    return int(qs["page"][0]) if "page" in qs else None
