from ..client import PaperlessError, PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp
from ._common import simple_bulk_delete, simple_delete

PATH = "/api/tags/"

# Parameter names below intentionally mirror barryw/PaperlessMCP's C# camelCase
# tool signatures exactly (pageSize, matchingAlgorithm, ...), not Python's
# snake_case convention — clients call this server by parameter name.


@mcp.tool(name="paperless_tags_list", description="List all tags with pagination.")
async def paperless_tags_list(
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


@mcp.tool(name="paperless_tags_get", description="Get a tag by its ID.")
async def paperless_tags_get(id: int) -> dict:  # noqa: A002 - matches C# param name
    client = await get_client()
    try:
        tag = await client.get_or_none(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if tag is None:
        return error(ErrorCodes.NOT_FOUND, f"Tag with ID {id} not found", client.base_url)
    return ok(tag, client.base_url)


@mcp.tool(name="paperless_tags_create", description="Create a new tag.")
async def paperless_tags_create(
    name: str,
    color: str | None = None,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
    isInboxTag: bool | None = None,
    parent: int | None = None,
) -> dict:
    client = await get_client()
    body = {
        "name": name,
        "color": color,
        "match": match,
        "matching_algorithm": matchingAlgorithm,
        "is_inbox_tag": isInboxTag,
        "parent": parent,
    }
    body = {k: v for k, v in body.items() if v is not None}
    try:
        tag = await client.post(PATH, body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            f"Failed to create tag: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
            details={"status_code": exc.status_code, "response_body": exc.body},
        )
    return ok(tag, client.base_url)


@mcp.tool(name="paperless_tags_update", description="Update an existing tag.")
async def paperless_tags_update(
    id: int,  # noqa: A002
    name: str | None = None,
    color: str | None = None,
    match: str | None = None,
    matchingAlgorithm: int | None = None,
    isInboxTag: bool | None = None,
    parent: int | None = None,
    clearParent: bool = False,
) -> dict:
    client = await get_client()
    if parent is not None and clearParent:
        return error(
            ErrorCodes.VALIDATION, "parent and clear_parent cannot be used together", client.base_url
        )

    body = {
        "name": name,
        "color": color,
        "match": match,
        "matching_algorithm": matchingAlgorithm,
        "is_inbox_tag": isInboxTag,
    }
    body = {k: v for k, v in body.items() if v is not None}
    if clearParent:
        body["parent"] = None
    elif parent is not None:
        body["parent"] = parent

    try:
        tag = await client.patch(f"{PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError:
        tag = None

    if tag is None:
        return error(
            ErrorCodes.NOT_FOUND, f"Tag with ID {id} not found or update failed", client.base_url
        )
    return ok(tag, client.base_url)


@mcp.tool(name="paperless_tags_delete", description="Delete a tag. Requires explicit confirmation.")
async def paperless_tags_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    return await simple_delete(
        client,
        f"{PATH}{id}/",
        lambda: client.get_or_none(f"{PATH}{id}/"),
        not_found_message=f"Tag with ID {id} not found",
        fail_message=f"Failed to delete tag with ID {id}",
        id_field="tag_id",
        id_value=id,
        confirm=confirm,
        dry_run_details=lambda tag: {
            "tag_id": id,
            "name": tag.get("name"),
            "document_count": tag.get("document_count"),
        },
    )


@mcp.tool(
    name="paperless_tags_bulk_delete",
    description="Delete multiple tags. Supports dry run mode.",
)
async def paperless_tags_bulk_delete(
    tagIds: str, dryRun: bool = True, confirm: bool = False
) -> dict:
    client = await get_client()
    return await simple_bulk_delete(
        client,
        tagIds,
        dryRun,
        confirm,
        "tags",
        invalid_ids_message="No valid tag IDs provided",
    )
