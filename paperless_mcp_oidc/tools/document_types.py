from ..client import PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp
from ._common import simple_bulk_delete, simple_delete

PATH = "/api/document_types/"


@mcp.tool(name="paperless_document_types_list", description="List all document types with pagination.")
async def paperless_document_types_list(
    page: int = 1,
    pageSize: int = 25,
    ordering: str | None = None,
) -> dict:
    client = await get_client()
    effective = client.effective_page_size(pageSize)
    params: dict = {"page": page, "page_size": effective}
    if ordering:
        params["ordering"] = ordering
    try:
        data = await client.get_paginated(PATH, params=params)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    return ok(
        data["results"],
        client.base_url,
        page=page,
        page_size=effective,
        total=data.get("count"),
        next=data.get("next"),
    )


@mcp.tool(name="paperless_document_types_get", description="Get a document type by its ID.")
async def paperless_document_types_get(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        document_type = await client.get_or_none(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if document_type is None:
        return error(ErrorCodes.NOT_FOUND, f"Document type with ID {id} not found", client.base_url)
    return ok(document_type, client.base_url)


@mcp.tool(name="paperless_document_types_create", description="Create a new document type.")
async def paperless_document_types_create(
    name: str,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        document_type = await client.post(PATH, body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        document_type = None
    if document_type is None:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to create document type", client.base_url)
    return ok(document_type, client.base_url)


@mcp.tool(name="paperless_document_types_update", description="Update an existing document type.")
async def paperless_document_types_update(
    id: int,  # noqa: A002
    name: str | None = None,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        document_type = await client.patch(f"{PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        document_type = None
    if document_type is None:
        return error(
            ErrorCodes.NOT_FOUND,
            f"Document type with ID {id} not found or update failed",
            client.base_url,
        )
    return ok(document_type, client.base_url)


@mcp.tool(
    name="paperless_document_types_delete",
    description="Delete a document type. Requires explicit confirmation.",
)
async def paperless_document_types_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    return await simple_delete(
        client,
        f"{PATH}{id}/",
        lambda: client.get_or_none(f"{PATH}{id}/"),
        not_found_message=f"Document type with ID {id} not found",
        fail_message=f"Failed to delete document type with ID {id}",
        id_field="document_type_id",
        id_value=id,
        confirm=confirm,
        dry_run_details=lambda dt: {
            "document_type_id": id,
            "name": dt.get("name"),
            "document_count": dt.get("document_count"),
        },
    )


@mcp.tool(
    name="paperless_document_types_bulk_delete",
    description="Delete multiple document types. Supports dry run mode.",
)
async def paperless_document_types_bulk_delete(
    documentTypeIds: str, dryRun: bool = True, confirm: bool = False
) -> dict:
    client = await get_client()
    return await simple_bulk_delete(
        client,
        documentTypeIds,
        dryRun,
        confirm,
        "document_types",
        invalid_ids_message="No valid document type IDs provided",
    )
