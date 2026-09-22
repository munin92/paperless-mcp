from ..client import PaperlessError, PaperlessNotAllowed
from ..responses import ErrorCodes, error, not_allowed_error, ok
from ..server import get_client, mcp


@mcp.tool(
    name="paperless_ping",
    description=(
        "Verify connectivity and authentication with the Paperless-ngx instance. "
        "Returns server version if available."
    ),
)
async def paperless_ping() -> dict:
    client = await get_client()
    try:
        status = await client.status()
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            str(exc) or "Failed to connect to Paperless instance",
            client.base_url,
        )

    if status is None:
        return error(ErrorCodes.UPSTREAM_ERROR, "Failed to connect to Paperless instance", client.base_url)

    version = status.get("pngx_version")
    return ok({"connected": True, "version": version}, client.base_url)


@mcp.tool(
    name="paperless_capabilities",
    description="Return supported API endpoints and detected Paperless-ngx version information.",
)
async def paperless_capabilities() -> dict:
    client = await get_client()
    status = None
    try:
        status = await client.status()
    except (PaperlessNotAllowed, PaperlessError):
        status = None

    version = status.get("pngx_version") if status else None

    capabilities = {
        "connected": status is not None,
        "version": version,
        "endpoints": {
            "documents": {
                "search": "/api/documents/",
                "get": "/api/documents/{id}/",
                "upload": "/api/documents/post_document/",
                "update": "/api/documents/{id}/",
                "delete": "/api/documents/{id}/",
                "download": "/api/documents/{id}/download/",
                "preview": "/api/documents/{id}/preview/",
                "thumbnail": "/api/documents/{id}/thumb/",
                "bulk_edit": "/api/documents/bulk_edit/",
            },
            "tags": {
                "list": "/api/tags/",
                "get": "/api/tags/{id}/",
                "create": "/api/tags/",
                "update": "/api/tags/{id}/",
                "delete": "/api/tags/{id}/",
            },
            "correspondents": {
                "list": "/api/correspondents/",
                "get": "/api/correspondents/{id}/",
                "create": "/api/correspondents/",
                "update": "/api/correspondents/{id}/",
                "delete": "/api/correspondents/{id}/",
            },
            "document_types": {
                "list": "/api/document_types/",
                "get": "/api/document_types/{id}/",
                "create": "/api/document_types/",
                "update": "/api/document_types/{id}/",
                "delete": "/api/document_types/{id}/",
            },
            "storage_paths": {
                "list": "/api/storage_paths/",
                "get": "/api/storage_paths/{id}/",
                "create": "/api/storage_paths/",
                "update": "/api/storage_paths/{id}/",
                "delete": "/api/storage_paths/{id}/",
            },
            "custom_fields": {
                "list": "/api/custom_fields/",
                "get": "/api/custom_fields/{id}/",
                "create": "/api/custom_fields/",
                "update": "/api/custom_fields/{id}/",
                "delete": "/api/custom_fields/{id}/",
            },
            "bulk_operations": "/api/bulk_edit_objects/",
        },
        "bulk_edit_methods": [
            "set_correspondent",
            "set_document_type",
            "set_storage_path",
            "add_tag",
            "remove_tag",
            "modify_tags",
            "modify_custom_fields",
            "delete",
            "reprocess",
        ],
        "status": status,
    }
    return ok(capabilities, client.base_url)
