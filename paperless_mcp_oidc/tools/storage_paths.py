from ..client import PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp
from ._common import simple_bulk_delete, simple_delete

PATH = "/api/storage_paths/"


@mcp.tool(name="paperless_storage_paths_list", description="List all storage paths with pagination.")
async def paperless_storage_paths_list(
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


@mcp.tool(name="paperless_storage_paths_get", description="Get a storage path by its ID.")
async def paperless_storage_paths_get(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        storage_path = await client.get_or_none(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if storage_path is None:
        return error(ErrorCodes.NOT_FOUND, f"Storage path with ID {id} not found", client.base_url)
    return ok(storage_path, client.base_url)


@mcp.tool(name="paperless_storage_paths_create", description="Create a new storage path.")
async def paperless_storage_paths_create(
    name: str,
    path: str,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "path": path, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        storage_path = await client.post(PATH, body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        storage_path = None
    if storage_path is None:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to create storage path", client.base_url)
    return ok(storage_path, client.base_url)


@mcp.tool(name="paperless_storage_paths_update", description="Update an existing storage path.")
async def paperless_storage_paths_update(
    id: int,  # noqa: A002
    name: str | None = None,
    path: str | None = None,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
) -> dict:
    client = await get_client()
    body = {"name": name, "path": path, "match": match, "matching_algorithm": matchingAlgorithm}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        storage_path = await client.patch(f"{PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        storage_path = None
    if storage_path is None:
        return error(
            ErrorCodes.NOT_FOUND,
            f"Storage path with ID {id} not found or update failed",
            client.base_url,
        )
    return ok(storage_path, client.base_url)


@mcp.tool(
    name="paperless_storage_paths_delete",
    description="Delete a storage path. Requires explicit confirmation.",
)
async def paperless_storage_paths_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    return await simple_delete(
        client,
        f"{PATH}{id}/",
        lambda: client.get_or_none(f"{PATH}{id}/"),
        not_found_message=f"Storage path with ID {id} not found",
        fail_message=f"Failed to delete storage path with ID {id}",
        id_field="storage_path_id",
        id_value=id,
        confirm=confirm,
        dry_run_details=lambda sp: {
            "storage_path_id": id,
            "name": sp.get("name"),
            "path": sp.get("path"),
            "document_count": sp.get("document_count"),
        },
    )


@mcp.tool(
    name="paperless_storage_paths_bulk_delete",
    description="Delete multiple storage paths. Supports dry run mode.",
)
async def paperless_storage_paths_bulk_delete(
    storagePathIds: str, dryRun: bool = True, confirm: bool = False
) -> dict:
    client = await get_client()
    return await simple_bulk_delete(
        client,
        storagePathIds,
        dryRun,
        confirm,
        "storage_paths",
        invalid_ids_message="No valid storage path IDs provided",
    )
