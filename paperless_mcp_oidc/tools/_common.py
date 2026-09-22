"""Shared plumbing for the "simple CRUD" entity families (tags, correspondents,
document_types, storage_paths): identical delete/bulk_delete shape, ported
once from the matching *Tools.cs Delete/BulkDelete pair instead of four times.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..client import PaperlessClient, PaperlessError, PaperlessNotAllowed
from ..parsing import parse_int_array
from ..responses import ErrorCodes, error, not_allowed_error, ok


async def simple_delete(
    client: PaperlessClient,
    path: str,
    get_fn: Callable[[], Awaitable[dict | None]],
    *,
    not_found_message: str,
    fail_message: str,
    id_field: str,
    id_value: int,
    confirm: bool,
    dry_run_details: Callable[[dict], dict],
) -> dict:
    if not confirm:
        try:
            obj = await get_fn()
        except PaperlessNotAllowed:
            return not_allowed_error(client.base_url)
        if obj is None:
            return error(ErrorCodes.NOT_FOUND, not_found_message, client.base_url)
        return error(
            ErrorCodes.CONFIRMATION_REQUIRED,
            "Deletion requires confirm=true. This is a dry run showing what would be deleted.",
            client.base_url,
            details=dry_run_details(obj),
        )

    try:
        success = await client.delete(path)
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError:
        success = False

    if not success:
        return error(ErrorCodes.UPSTREAM_ERROR, fail_message, client.base_url)

    return ok({"deleted": True, id_field: id_value}, client.base_url)


def bulk_result(ids: list[int], *, executed: bool, warnings: list[str]) -> dict[str, Any]:
    return {
        "affected_ids": ids,
        "current_values": None,
        "proposed_changes": None,
        "warnings": warnings,
        "executed": executed,
    }


async def simple_bulk_delete(
    client: PaperlessClient,
    ids_raw: str,
    dry_run: bool,
    confirm: bool,
    object_type: str,
    *,
    invalid_ids_message: str,
) -> dict:
    ids = parse_int_array(ids_raw)
    if not ids:
        return error(ErrorCodes.VALIDATION, invalid_ids_message, client.base_url)

    if dry_run or not confirm:
        warning = (
            "This is a dry run. Set dry_run=false and confirm=true to execute."
            if dry_run
            else "Set confirm=true to execute the operation."
        )
        return ok(bulk_result(ids, executed=False, warnings=[warning]), client.base_url)

    try:
        await client.bulk_edit_objects(ids, object_type, "delete")
    except PaperlessNotAllowed:
        return not_allowed_error(client.base_url)
    except PaperlessError as exc:
        return error(
            ErrorCodes.UPSTREAM_ERROR,
            f"Bulk delete operation failed: HTTP {exc.status_code}: {exc.body}",
            client.base_url,
        )

    return ok(bulk_result(ids, executed=True, warnings=[]), client.base_url)
