from ..client import PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp
from ._common import simple_bulk_delete, simple_delete

PATH = "/api/correspondents/"


@mcp.tool(name="paperless_correspondents_list", description="List all correspondents with pagination.")
async def paperless_correspondents_list(
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


@mcp.tool(name="paperless_correspondents_get", description="Get a correspondent by its ID.")
async def paperless_correspondents_get(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        correspondent = await client.get_or_none(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if correspondent is None:
        return error(ErrorCodes.NOT_FOUND, f"Correspondent with ID {id} not found", client.base_url)
    return ok(correspondent, client.base_url)


@mcp.tool(name="paperless_correspondents_create", description="Create a new correspondent.")
async def paperless_correspondents_create(
    name: str,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        correspondent = await client.post(PATH, body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        correspondent = None
    if correspondent is None:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to create correspondent", client.base_url)
    return ok(correspondent, client.base_url)


@mcp.tool(name="paperless_correspondents_update", description="Update an existing correspondent.")
async def paperless_correspondents_update(
    id: int,  # noqa: A002
    name: str | None = None,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        correspondent = await client.patch(f"{PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        correspondent = None
    if correspondent is None:
        return error(
            ErrorCodes.NOT_FOUND,
            f"Correspondent with ID {id} not found or update failed",
            client.base_url,
        )
    return ok(correspondent, client.base_url)


@mcp.tool(
    name="paperless_correspondents_delete",
    description="Delete a correspondent. Requires explicit confirmation.",
)
async def paperless_correspondents_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    return await simple_delete(
        client,
        f"{PATH}{id}/",
        lambda: client.get_or_none(f"{PATH}{id}/"),
        not_found_message=f"Correspondent with ID {id} not found",
        fail_message=f"Failed to delete correspondent with ID {id}",
        id_field="correspondent_id",
        id_value=id,
        confirm=confirm,
        dry_run_details=lambda c: {
            "correspondent_id": id,
            "name": c.get("name"),
            "document_count": c.get("document_count"),
        },
    )


@mcp.tool(
    name="paperless_correspondents_bulk_delete",
    description="Delete multiple correspondents. Supports dry run mode.",
)
async def paperless_correspondents_bulk_delete(
    correspondentIds: str, dryRun: bool = True, confirm: bool = False
) -> dict:
    client = await get_client()
    return await simple_bulk_delete(
        client,
        correspondentIds,
        dryRun,
        confirm,
        "correspondents",
        invalid_ids_message="No valid correspondent IDs provided",
    )
