"""One happy-path request-shape test per remaining tool family (tags,
correspondents, document_types, storage_paths, custom_fields), ported from
TagToolsTests.cs / CorrespondentToolsTests.cs / DocumentTypeToolsTests.cs /
StoragePathToolsTests.cs / CustomFieldToolsTests.cs."""

import json

import pytest
import respx
from conftest import async_return
from httpx import Response

from paperless_mcp_oidc.responses import ErrorCodes
from paperless_mcp_oidc.tools import correspondents, custom_fields, document_types, storage_paths, tags

BASE = "https://paperless.example.com"


@pytest.fixture(autouse=True)
def _wire_clients(monkeypatch, paperless_client):
    for mod in (tags, correspondents, document_types, storage_paths, custom_fields):
        monkeypatch.setattr(mod, "get_client", async_return(paperless_client))


# --- tags -------------------------------------------------------------------


@respx.mock
async def test_tags_list_respects_max_page_size():
    paperless_client = await tags.get_client()
    paperless_client.max_page_size = 10
    route = respx.get(f"{BASE}/api/tags/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "previous": None, "results": []})
    )
    result = await tags.paperless_tags_list(page=2, pageSize=50)
    assert route.calls.last.request.url.params["page_size"] == "10"
    assert result["meta"]["page_size"] == 10


@respx.mock
async def test_tags_create_sends_expected_body():
    route = respx.post(f"{BASE}/api/tags/").mock(
        return_value=Response(200, json={"id": 1, "name": "Invoices", "document_count": 0})
    )
    result = await tags.paperless_tags_create(name="Invoices", isInboxTag=True)
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "Invoices", "is_inbox_tag": True}
    assert result["ok"] is True


async def test_tags_update_rejects_parent_and_clear_parent_together():
    result = await tags.paperless_tags_update(id=1, parent=2, clearParent=True)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


@respx.mock
async def test_tags_delete_dry_run_then_confirm():
    respx.get(f"{BASE}/api/tags/1/").mock(
        return_value=Response(200, json={"id": 1, "name": "x", "document_count": 3})
    )
    dry = await tags.paperless_tags_delete(id=1)
    assert dry["error"]["code"] == ErrorCodes.CONFIRMATION_REQUIRED

    respx.delete(f"{BASE}/api/tags/1/").mock(return_value=Response(204))
    done = await tags.paperless_tags_delete(id=1, confirm=True)
    assert done["ok"] is True


@respx.mock
async def test_tags_bulk_delete_executes_with_confirm():
    route = respx.post(f"{BASE}/api/bulk_edit_objects/").mock(return_value=Response(200, json={}))
    result = await tags.paperless_tags_bulk_delete(tagIds="1,2", dryRun=False, confirm=True)
    body = json.loads(route.calls.last.request.content)
    assert body == {"objects": [1, 2], "object_type": "tags", "operation": "delete"}
    assert result["result"]["executed"] is True


# --- correspondents -----------------------------------------------------------


@respx.mock
async def test_correspondents_create_sends_expected_body():
    route = respx.post(f"{BASE}/api/correspondents/").mock(
        return_value=Response(200, json={"id": 3, "name": "Acme"})
    )
    result = await correspondents.paperless_correspondents_create(name="Acme", matchingAlgorithm=2)
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "Acme", "matching_algorithm": 2}
    assert result["ok"] is True


@respx.mock
async def test_correspondents_bulk_delete_dry_run_by_default():
    result = await correspondents.paperless_correspondents_bulk_delete(correspondentIds="5,6")
    assert result["result"]["executed"] is False
    assert result["result"]["affected_ids"] == [5, 6]


# --- document types -----------------------------------------------------------


@respx.mock
async def test_document_types_get_not_found():
    respx.get(f"{BASE}/api/document_types/9/").mock(return_value=Response(404))
    result = await document_types.paperless_document_types_get(id=9)
    assert result["error"]["code"] == ErrorCodes.NOT_FOUND


@respx.mock
async def test_document_types_update_sends_only_provided_fields():
    route = respx.patch(f"{BASE}/api/document_types/9/").mock(
        return_value=Response(200, json={"id": 9, "name": "Invoice"})
    )
    await document_types.paperless_document_types_update(id=9, name="Invoice")
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "Invoice"}


# --- storage paths --------------------------------------------------------


@respx.mock
async def test_storage_paths_create_includes_path_field():
    route = respx.post(f"{BASE}/api/storage_paths/").mock(
        return_value=Response(200, json={"id": 1, "name": "By year", "path": "{created_year}/"})
    )
    await storage_paths.paperless_storage_paths_create(name="By year", path="{created_year}/")
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "By year", "path": "{created_year}/"}


@respx.mock
async def test_storage_paths_delete_dry_run_includes_path_in_details():
    respx.get(f"{BASE}/api/storage_paths/1/").mock(
        return_value=Response(200, json={"id": 1, "name": "x", "path": "{y}/", "document_count": 1})
    )
    result = await storage_paths.paperless_storage_paths_delete(id=1)
    assert result["error"]["details"]["path"] == "{y}/"


# --- custom fields --------------------------------------------------------


@respx.mock
async def test_custom_fields_create_select_type():
    route = respx.post(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json={"id": 1, "name": "Priority", "data_type": "select"})
    )
    await custom_fields.paperless_custom_fields_create(
        name="Priority", dataType="select", selectOptions="Low,High"
    )
    body = json.loads(route.calls.last.request.content)
    assert body["extra_data"]["select_options"] == [{"label": "Low"}, {"label": "High"}]


@respx.mock
async def test_custom_fields_assign_parses_integer_value():
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x", "custom_fields": []})
    )
    respx.get(f"{BASE}/api/custom_fields/7/").mock(
        return_value=Response(200, json={"id": 7, "name": "Count", "data_type": "integer"})
    )
    route = respx.patch(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42}))
    result = await custom_fields.paperless_custom_fields_assign(documentId=42, fieldId=7, value="12")
    body = json.loads(route.calls.last.request.content)
    assert body == {"custom_fields": [{"field": 7, "value": 12}]}
    assert result["result"]["value"] == 12


@respx.mock
async def test_custom_fields_assign_document_link_parses_id_list():
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x", "custom_fields": []})
    )
    respx.get(f"{BASE}/api/custom_fields/7/").mock(
        return_value=Response(200, json={"id": 7, "name": "Related", "data_type": "documentlink"})
    )
    route = respx.patch(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42}))
    await custom_fields.paperless_custom_fields_assign(documentId=42, fieldId=7, value="1, 2, 3")
    body = json.loads(route.calls.last.request.content)
    assert body["custom_fields"][0]["value"] == [1, 2, 3]
