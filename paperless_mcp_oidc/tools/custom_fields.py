from collections import defaultdict, deque
from typing import Any

from ..client import PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp

PATH = "/api/custom_fields/"
DOCUMENTS_PATH = "/api/documents/"

SELECT = "select"
MONETARY = "monetary"
BOOLEAN = "boolean"
INTEGER = "integer"
FLOAT = "float"
DATE = "date"
DOCUMENT_LINK = "documentlink"


def _build_select_options(select_options: str, existing: list[dict] | None = None) -> list[dict]:
    existing_by_label: dict[str, deque] = defaultdict(deque)
    for opt in existing or []:
        existing_by_label[opt.get("label")].append(opt)

    result = []
    for label in (part.strip() for part in select_options.split(",")):
        if not label:
            continue
        bucket = existing_by_label.get(label)
        if bucket:
            existing_opt = bucket.popleft()
            result.append({"id": existing_opt.get("id"), "label": label})
        else:
            result.append({"label": label})
    return result


def _parse_document_link_ids(value: str) -> list[int] | None:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        return None
    ids = []
    for part in parts:
        try:
            ids.append(int(part))
        except ValueError:
            return None
    return ids


@mcp.tool(
    name="paperless_custom_fields_list",
    description="List all custom field definitions with pagination.",
)
async def paperless_custom_fields_list(page: int = 1, pageSize: int = 25) -> dict:
    client = await get_client()
    effective = client.effective_page_size(pageSize)
    try:
        data = await client.get_paginated(PATH, params={"page": page, "page_size": effective})
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


@mcp.tool(name="paperless_custom_fields_get", description="Get a custom field definition by its ID.")
async def paperless_custom_fields_get(id: int) -> dict:  # noqa: A002
    client = await get_client()
    try:
        field = await client.get_or_none(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if field is None:
        return error(ErrorCodes.NOT_FOUND, f"Custom field with ID {id} not found", client.base_url)
    return ok(field, client.base_url)


@mcp.tool(name="paperless_custom_fields_create", description="Create a new custom field definition.")
async def paperless_custom_fields_create(
    name: str,
    dataType: str,
    selectOptions: str | None = None,
    defaultCurrency: str | None = None,
) -> dict:
    client = await get_client()
    extra_data: dict[str, Any] | None = None
    if dataType == SELECT and selectOptions:
        extra_data = {"select_options": _build_select_options(selectOptions)}
    elif dataType == MONETARY and defaultCurrency:
        extra_data = {"default_currency": defaultCurrency}

    body: dict[str, Any] = {"name": name, "data_type": dataType}
    if extra_data is not None:
        body["extra_data"] = extra_data

    try:
        field = await client.post(PATH, body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        field = None
    if field is None:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to create custom field", client.base_url)
    return ok(field, client.base_url)


@mcp.tool(
    name="paperless_custom_fields_update",
    description="Update an existing custom field definition.",
)
async def paperless_custom_fields_update(
    id: int,  # noqa: A002
    name: str | None = None,
    selectOptions: str | None = None,
    defaultCurrency: str | None = None,
) -> dict:
    client = await get_client()
    extra_data: dict[str, Any] | None = None

    if selectOptions:
        try:
            existing = await client.get_or_none(f"{PATH}{id}/")
        except PaperlessNotAllowed:
            return not_allowed_error(client.base_url)
        if existing is None:
            return error(
                ErrorCodes.NOT_FOUND,
                f"Custom field with ID {id} not found or update failed",
                client.base_url,
            )
        existing_options = (existing.get("extra_data") or {}).get("select_options")
        extra_data = {"select_options": _build_select_options(selectOptions, existing_options)}
    elif defaultCurrency:
        extra_data = {"default_currency": defaultCurrency}

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if extra_data is not None:
        body["extra_data"] = extra_data

    try:
        field = await client.patch(f"{PATH}{id}/", body)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        field = None
    if field is None:
        return error(
            ErrorCodes.NOT_FOUND, f"Custom field with ID {id} not found or update failed", client.base_url
        )
    return ok(field, client.base_url)


@mcp.tool(
    name="paperless_custom_fields_delete",
    description="Delete a custom field definition. Requires explicit confirmation.",
)
async def paperless_custom_fields_delete(id: int, confirm: bool = False) -> dict:  # noqa: A002
    client = await get_client()
    if not confirm:
        try:
            field = await client.get_or_none(f"{PATH}{id}/")
        except PaperlessNotAllowed:
            return not_allowed_error(client.base_url)
        if field is None:
            return error(ErrorCodes.NOT_FOUND, f"Custom field with ID {id} not found", client.base_url)
        return error(
            ErrorCodes.CONFIRMATION_REQUIRED,
            "Deletion requires confirm=true. This is a dry run showing what would be deleted.",
            client.base_url,
            details={"custom_field_id": id, "name": field.get("name"), "data_type": field.get("data_type")},
        )

    try:
        success = await client.delete(f"{PATH}{id}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if not success:
        return error(
            ErrorCodes.UPSTREAM_ERROR, f"Failed to delete custom field with ID {id}", client.base_url
        )
    return ok({"deleted": True, "custom_field_id": id}, client.base_url)


@mcp.tool(name="paperless_custom_fields_assign", description="Assign a custom field value to a document.")
async def paperless_custom_fields_assign(documentId: int, fieldId: int, value: str) -> dict:
    client = await get_client()
    try:
        document = await client.get_or_none(f"{DOCUMENTS_PATH}{documentId}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if document is None:
        return error(ErrorCodes.NOT_FOUND, f"Document with ID {documentId} not found", client.base_url)

    try:
        field = await client.get_or_none(f"{PATH}{fieldId}/")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    if field is None:
        return error(ErrorCodes.NOT_FOUND, f"Custom field with ID {fieldId} not found", client.base_url)

    data_type = field.get("data_type")
    parsed_value: Any
    truthy = {"true", "1", "yes"}
    falsy = {"false", "0", "no"}
    if data_type == BOOLEAN:
        lowered = value.strip().lower()
        parsed_value = True if lowered in truthy else False if lowered in falsy else None
    elif data_type == INTEGER:
        try:
            parsed_value = int(value)
        except ValueError:
            parsed_value = None
    elif data_type == FLOAT:
        try:
            parsed_value = float(value)
        except ValueError:
            parsed_value = None
    elif data_type == DATE:
        parsed_value = value
    elif data_type == DOCUMENT_LINK:
        parsed_value = _parse_document_link_ids(value)
    else:
        parsed_value = value

    custom_fields = list(document.get("custom_fields") or [])
    replaced = False
    for i, cf in enumerate(custom_fields):
        if cf.get("field") == fieldId:
            custom_fields[i] = {"field": fieldId, "value": parsed_value}
            replaced = True
            break
    if not replaced:
        custom_fields.append({"field": fieldId, "value": parsed_value})

    try:
        updated = await client.patch(f"{DOCUMENTS_PATH}{documentId}/", {"custom_fields": custom_fields})
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except Exception:
        updated = None

    if updated is None:
        return error(
            ErrorCodes.UPSTREAM_ERROR, "Failed to assign custom field to document", client.base_url
        )

    return ok(
        {
            "document_id": documentId,
            "field_id": fieldId,
            "field_name": field.get("name"),
            "value": parsed_value,
            "message": "Custom field assigned successfully",
        },
        client.base_url,
    )
