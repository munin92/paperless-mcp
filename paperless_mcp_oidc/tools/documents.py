"""Document tools — ported from PaperlessMCP/Tools/DocumentTools.cs.

Parameter names intentionally mirror the C# camelCase tool signatures exactly
(documentType, archiveSerialNumber, ...), not Python's snake_case convention.
"""

from __future__ import annotations

import base64
import contextlib
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from ..client import (
    MAX_INLINE_BASE64_BYTES,
    PaperlessError,
    PaperlessNotAllowed,
)
from ..parsing import (
    fallback_title,
    parse_date,
    parse_int_array,
    safe_export_filename,
    title_is_valid,
    with_document_id,
)
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp
from ._common import bulk_result, simple_delete

DOCUMENTS_PATH = "/api/documents/"

VALID_BULK_OPERATIONS = (
    "add_tag",
    "remove_tag",
    "set_correspondent",
    "set_document_type",
    "set_storage_path",
    "delete",
    "reprocess",
)


def _summarize(doc: dict, include_content: bool, content_max_length: int | None) -> dict:
    content = None
    raw = doc.get("content")
    if include_content and raw:
        truncate = content_max_length and len(raw) > content_max_length
        content = raw[:content_max_length] + "..." if truncate else raw
    return {
        "id": doc.get("id"),
        "correspondent": doc.get("correspondent"),
        "document_type": doc.get("document_type"),
        "storage_path": doc.get("storage_path"),
        "title": doc.get("title", ""),
        "content": content,
        "tags": doc.get("tags", []),
        "created": doc.get("created"),
        "modified": doc.get("modified"),
        "added": doc.get("added"),
        "archive_serial_number": doc.get("archive_serial_number"),
        "original_file_name": doc.get("original_file_name"),
        "__search_hit__": doc.get("__search_hit__"),
    }


def _download_info(client, doc_id: int, doc: dict) -> dict:
    base = client.base_url
    return {
        "id": doc_id,
        "title": doc.get("title", ""),
        "original_file_name": doc.get("original_file_name"),
        "download_url": f"{base}/api/documents/{doc_id}/download/",
        "preview_url": f"{base}/api/documents/{doc_id}/preview/",
        "thumbnail_url": f"{base}/api/documents/{doc_id}/thumb/",
    }


def _as_archived_name(original_file_name: str | None) -> str | None:
    """Restates an original file name with the archived version's extension:
    Paperless archives to PDF, so page.jpg comes back as page.pdf."""
    if not original_file_name or not original_file_name.strip():
        return None
    name = PurePosixPath(original_file_name.strip()).name
    return str(PurePosixPath(name).with_suffix(".pdf")) if name else None


@mcp.tool(
    name="paperless_documents_search",
    description="Search for documents with full-text search and filters. Supports pagination.",
)
async def paperless_documents_search(
    query: str | None = None,
    tags: str | None = None,
    tagsExclude: str | None = None,
    correspondent: int | None = None,
    documentType: int | None = None,
    storagePath: int | None = None,
    createdAfter: str | None = None,
    createdBefore: str | None = None,
    addedAfter: str | None = None,
    addedBefore: str | None = None,
    archiveSerialNumber: int | None = None,
    page: int = 1,
    pageSize: int = 25,
    ordering: str | None = None,
    customFieldQuery: str | None = None,
    includeContent: bool = False,
    contentMaxLength: int = 500,
) -> dict:
    client = await get_client()
    effective = client.effective_page_size(pageSize)

    params: list[tuple[str, str]] = []
    if query:
        params.append(("query", query))
    for t in parse_int_array(tags) or []:
        params.append(("tags__id__in", str(t)))
    for t in parse_int_array(tagsExclude) or []:
        params.append(("tags__id__none", str(t)))
    if correspondent is not None:
        params.append(("correspondent__id", str(correspondent)))
    if documentType is not None:
        params.append(("document_type__id", str(documentType)))
    if storagePath is not None:
        params.append(("storage_path__id", str(storagePath)))
    if (d := parse_date(createdAfter)) is not None:
        params.append(("created__date__gt", d))
    if (d := parse_date(createdBefore)) is not None:
        params.append(("created__date__lt", d))
    if (d := parse_date(addedAfter)) is not None:
        params.append(("added__date__gt", d))
    if (d := parse_date(addedBefore)) is not None:
        params.append(("added__date__lt", d))
    if archiveSerialNumber is not None:
        params.append(("archive_serial_number", str(archiveSerialNumber)))
    if customFieldQuery and customFieldQuery.strip():
        params.append(("custom_field_query", customFieldQuery))
    params.append(("page", str(page)))
    params.append(("page_size", str(effective)))
    if ordering:
        params.append(("ordering", ordering))

    try:
        data = await client.search_documents(params)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            f"Failed to search documents: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
            details={"status_code": exc.status_code},
        )

    summaries = [
        _summarize(d, includeContent, contentMaxLength if contentMaxLength > 0 else None)
        for d in data.get("results", [])
    ]

    return ok(
        summaries,
        client.base_url,
        page=page,
        page_size=effective,
        total=data.get("count"),
        next=data.get("next"),
    )


@mcp.tool(name="paperless_documents_get", description="Get a document by its ID.")
async def paperless_documents_get(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if doc is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)
    return ok(doc, client.base_url)


@mcp.tool(
    name="paperless_documents_download",
    description=(
        "Get download URLs for a document's original file, preview, and thumbnail. Set "
        "returnBase64=true to also inline the file bytes as base64, but only for tiny files: "
        "use paperless_documents_export_to_outbox for anything real."
    ),
)
async def paperless_documents_download(id: int, returnBase64: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    try:
        doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if doc is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)

    info = _download_info(client, id, doc)
    if not returnBase64:
        return ok(info, client.base_url)

    try:
        content, content_type, _ = await client.open_document_file(id)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR, f"Failed to download file for document {id}: {exc}", client.base_url
        )

    if len(content) > MAX_INLINE_BASE64_BYTES:
        return error(
            ErrorCodes.VALIDATION,
            f"File is {len(content)} bytes; too large to inline as base64 "
            f"(limit {MAX_INLINE_BASE64_BYTES}). Use paperless_documents_export_to_outbox instead.",
            client.base_url,
        )

    return ok(
        {
            "id": id,
            "title": info["title"],
            "original_file_name": info["original_file_name"],
            "download_url": info["download_url"],
            "preview_url": info["preview_url"],
            "thumbnail_url": info["thumbnail_url"],
            "mime_type": content_type,
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        client.base_url,
    )


@mcp.tool(
    name="paperless_documents_export_to_outbox",
    description=(
        "Download a document's file server-side and write it into the shared outbox directory, "
        "returning its path, filename and MIME type. Lets another MCP server (e.g. Gmail) attach "
        "the file by path WITHOUT the bytes passing through the model context. Prefer this over "
        "base64 for anything but tiny files."
    ),
)
async def paperless_documents_export_to_outbox(
    id: int,  # noqa: A002
    filename: str | None = None,
    original: bool = False,
) -> dict:
    client = await get_client()
    try:
        doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if doc is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)

    try:
        content, content_type, suggested_name = await client.open_document_file(id, original=original)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR, f"Failed to download file for document {id}: {exc}", client.base_url
        )

    derived = (
        doc.get("original_file_name")
        if original
        else doc.get("archived_file_name") or _as_archived_name(doc.get("original_file_name"))
    )
    raw_name = filename or suggested_name or derived or "document.pdf"
    safe_name = safe_export_filename(raw_name)
    if not filename:
        safe_name = with_document_id(safe_name, id)

    outbox_dir = Path(client.outbox_dir)
    full_path = outbox_dir / safe_name
    tmp_path = outbox_dir / f".{safe_name}.{uuid.uuid4().hex}.part"
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(content)
        tmp_path.replace(full_path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        return error(
            ErrorCodes.UPSTREAM_ERROR, f"Failed to write document {id} to outbox: {exc}", client.base_url
        )

    return ok(
        {
            "id": id,
            "path": str(full_path),
            "filename": safe_name,
            "mime_type": content_type,
            "size_bytes": len(content),
        },
        client.base_url,
    )


@mcp.tool(name="paperless_documents_preview", description="Get the preview URL for a document.")
async def paperless_documents_preview(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if doc is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)
    info = _download_info(client, id, doc)
    return ok({"id": id, "title": doc.get("title", ""), "preview_url": info["preview_url"]}, client.base_url)


@mcp.tool(name="paperless_documents_thumbnail", description="Get the thumbnail URL for a document.")
async def paperless_documents_thumbnail(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if doc is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)
    info = _download_info(client, id, doc)
    return ok(
        {"id": id, "title": doc.get("title", ""), "thumbnail_url": info["thumbnail_url"]}, client.base_url
    )


@mcp.tool(
    name="paperless_documents_upload",
    description=(
        "Upload a new document to Paperless-ngx. Provide file content as base64. For large "
        "files, use paperless_documents_upload_from_path instead."
    ),
)
async def paperless_documents_upload(
    fileContent: str,
    fileName: str,
    title: str | None = None,
    correspondent: int | None = None,
    documentType: int | None = None,
    storagePath: int | None = None,
    tags: str | None = None,
    archiveSerialNumber: int | None = None,
    created: str | None = None,
) -> dict:
    client = await get_client()

    # Validate the *effective* title (explicit, or the filename-stem fallback
    # Paperless would derive) — a too-long title is silently truncated on
    # ingestion otherwise. See parsing.title_is_valid.
    effective_title = title if title else fallback_title(fileName)
    valid, message = title_is_valid(effective_title)
    if not valid:
        return error(ErrorCodes.VALIDATION, message, client.base_url)

    try:
        file_bytes = base64.b64decode(fileContent, validate=True)
    except Exception:
        return error(ErrorCodes.VALIDATION, "Invalid base64 file content", client.base_url)

    try:
        task_id = await client.upload_document(
            file_bytes,
            fileName,
            title=title,
            correspondent=correspondent,
            document_type=documentType,
            storage_path=storagePath,
            tags=parse_int_array(tags),
            archive_serial_number=archiveSerialNumber,
            created=parse_date(created),
        )
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError:
        task_id = None

    if not task_id:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to upload document", client.base_url)

    return ok(
        {"task_id": task_id, "status": "queued", "message": "Document uploaded and queued for processing"},
        client.base_url,
    )


@mcp.tool(
    name="paperless_documents_upload_from_path",
    description=(
        "Upload a document from a local file path. More reliable than base64 upload for large "
        "files. Includes automatic retries."
    ),
)
async def paperless_documents_upload_from_path(
    filePath: str,
    title: str | None = None,
    correspondent: int | None = None,
    documentType: int | None = None,
    storagePath: int | None = None,
    tags: str | None = None,
    archiveSerialNumber: int | None = None,
    created: str | None = None,
) -> dict:
    client = await get_client()

    path_str = filePath
    if path_str.startswith("~/"):
        path_str = str(Path.home() / path_str[2:])
    p = Path(path_str)

    if not p.is_absolute():
        return error(ErrorCodes.VALIDATION, "File path must be absolute", client.base_url)
    # Checked before existence, so the error never reveals which foreign files exist.
    if client.confine_paths_to_outbox:
        eigene = Path(client.outbox_dir).resolve()
        if not p.resolve().is_relative_to(eigene):
            return error(
                ErrorCodes.VALIDATION,
                f"File path must be inside your own outbox: {eigene}",
                client.base_url,
            )
    if not p.exists():
        return error(ErrorCodes.NOT_FOUND, f"File not found: {path_str}", client.base_url)

    # UploadFromPath sends the client-computed fallback title explicitly (unlike
    # Upload, which lets Paperless derive it server-side) — mirrors DocumentTools.cs.
    effective_title = title if title else fallback_title(p.name)
    valid, message = title_is_valid(effective_title)
    if not valid:
        return error(ErrorCodes.VALIDATION, message, client.base_url)

    try:
        task_id = await client.upload_document_from_path(
            p,
            title=effective_title,
            correspondent=correspondent,
            document_type=documentType,
            storage_path=storagePath,
            tags=parse_int_array(tags),
            archive_serial_number=archiveSerialNumber,
            created=parse_date(created),
        )
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(ErrorCodes.UPSTREAM_ERROR, str(exc) or "Failed to upload document", client.base_url)

    if not task_id:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to upload document", client.base_url)

    return ok(
        {
            "task_id": task_id,
            "status": "queued",
            "message": "Document uploaded and queued for processing",
            "file_name": p.name,
            "file_size": p.stat().st_size,
        },
        client.base_url,
    )


@mcp.tool(
    name="paperless_documents_update",
    description="Update document metadata (title, correspondent, type, tags, etc.).",
)
async def paperless_documents_update(
    id: int,  # noqa: A002
    title: str | None = None,
    correspondent: int | None = None,
    documentType: int | None = None,
    storagePath: int | None = None,
    tags: str | None = None,
    archiveSerialNumber: int | None = None,
    created: str | None = None,
    includeContent: bool = False,
) -> dict:
    client = await get_client()
    valid, message = title_is_valid(title)
    if not valid:
        return error(ErrorCodes.VALIDATION, message, client.base_url)

    # NOTE (mirrors DocumentTools.cs Update, verbatim quirk): correspondent/
    # documentType/storagePath == -1 is documented as "use -1 to clear", but
    # the C# request DTO ignores null fields when serializing, and -1 maps to
    # null just like "not provided" does — so -1 has the SAME wire effect as
    # omitting the field (it does NOT actually clear it on Paperless). Ported
    # verbatim rather than silently fixed, since fixing it here would diverge
    # from the reference server's actual behaviour.
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if correspondent is not None and correspondent != -1:
        body["correspondent"] = correspondent
    if documentType is not None and documentType != -1:
        body["document_type"] = documentType
    if storagePath is not None and storagePath != -1:
        body["storage_path"] = storagePath
    if (parsed_tags := parse_int_array(tags)) is not None:
        body["tags"] = parsed_tags
    if archiveSerialNumber is not None:
        body["archive_serial_number"] = archiveSerialNumber
    if (parsed_created := parse_date(created)) is not None:
        body["created"] = parsed_created

    try:
        doc = await client.patch(f"{DOCUMENTS_PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        code = ErrorCodes.NOT_FOUND if exc.status_code == 404 else ErrorCodes.UPSTREAM_ERROR
        return error(
            code,
            f"Failed to update document {id}: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
            details={"status_code": exc.status_code, "response_body": exc.body},
        )

    # Metadata-only updates don't need the OCR content echoed back — strip it
    # to keep bulk rename/retag workflows from burning tokens on unused content.
    if not includeContent and doc is not None:
        doc = {**doc, "content": ""}

    return ok(doc, client.base_url)


@mcp.tool(name="paperless_documents_delete", description="Delete a document. Requires explicit confirmation.")
async def paperless_documents_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    return await simple_delete(
        client,
        f"{DOCUMENTS_PATH}{id}/",
        lambda: client.get_or_none(f"{DOCUMENTS_PATH}{id}/"),
        not_found_message=f"Document with ID {id} not found",
        fail_message=f"Failed to delete document with ID {id}",
        id_field="document_id",
        id_value=id,
        confirm=confirm,
        dry_run_details=lambda d: {
            "document_id": id,
            "title": d.get("title"),
            "original_file_name": d.get("original_file_name"),
            "created": d.get("created"),
        },
    )


@mcp.tool(
    name="paperless_documents_bulk_update",
    description="Perform bulk operations on multiple documents. Supports dry run mode.",
)
async def paperless_documents_bulk_update(
    documentIds: str,
    operation: str,
    value: int | None = None,
    dryRun: bool = True,
    confirm: bool = False,
) -> dict:
    client = await get_client()
    ids = parse_int_array(documentIds)
    if not ids:
        return error(ErrorCodes.VALIDATION, "No valid document IDs provided", client.base_url)

    if operation not in VALID_BULK_OPERATIONS:
        return error(
            ErrorCodes.VALIDATION,
            f"Invalid operation. Valid operations: {', '.join(VALID_BULK_OPERATIONS)}",
            client.base_url,
        )

    if dryRun or not confirm:
        warning = (
            "This is a dry run. Set dry_run=false and confirm=true to execute."
            if dryRun
            else "Set confirm=true to execute the operation."
        )
        return ok(bulk_result(ids, executed=False, warnings=[warning]), client.base_url)

    parameters: dict[str, Any] | None = None
    if operation in ("add_tag", "remove_tag"):
        parameters = {"tag": value}
    elif operation == "set_correspondent":
        parameters = {"correspondent": value}
    elif operation == "set_document_type":
        parameters = {"document_type": value}
    elif operation == "set_storage_path":
        parameters = {"storage_path": value}

    try:
        await client.bulk_edit_documents(ids, operation, parameters)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            f"Bulk operation failed: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
        )

    return ok(bulk_result(ids, executed=True, warnings=[]), client.base_url)


@mcp.tool(
    name="paperless_documents_reprocess",
    description="Reprocess a document's OCR and content extraction.",
)
async def paperless_documents_reprocess(id: int, confirm: bool = False) -> dict:
    client = await get_client()
    if not confirm:
        try:
            doc = await client.get_or_none(f"{DOCUMENTS_PATH}{id}/")
        except PaperlessNotAllowed:
            return not_allowed_error(client.base_url)
        if doc is None:
            return error(ErrorCodes.NOT_FOUND, f"Document with ID {id} not found", client.base_url)
        return error(
            ErrorCodes.CONFIRMATION_REQUIRED,
            "Reprocessing requires confirm=true. This will re-run OCR on the document.",
            client.base_url,
            details={"document_id": id, "title": doc.get("title")},
        )

    try:
        await client.bulk_edit_documents([id], "reprocess")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            f"Failed to reprocess document with ID {id}: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
        )

    return ok(
        {"document_id": id, "status": "queued", "message": "Document queued for reprocessing"},
        client.base_url,
    )
