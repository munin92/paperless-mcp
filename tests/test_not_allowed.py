"""401/403 from Paperless must surface as a clean 'not allowed' error without
leaking any response body — across representative tools from every family
that touches the network directly."""

import pytest
import respx
from conftest import async_return
from httpx import Response

from paperless_mcp_oidc.responses import ErrorCodes
from paperless_mcp_oidc.tools import correspondents, documents, health, tags

BASE = "https://paperless.example.com"


@pytest.fixture(autouse=True)
def _wire_clients(monkeypatch, paperless_client):
    for mod in (tags, correspondents, documents, health):
        monkeypatch.setattr(mod, "get_client", async_return(paperless_client))


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_documents_get_not_allowed(status):
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(status, json={"detail": "secret internal detail"})
    )
    result = await documents.paperless_documents_get(id=42)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED
    assert "secret internal detail" not in str(result)


@respx.mock
async def test_tags_list_not_allowed():
    respx.get(f"{BASE}/api/tags/").mock(return_value=Response(403))
    result = await tags.paperless_tags_list()
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED


@respx.mock
async def test_tags_delete_confirm_not_allowed():
    respx.delete(f"{BASE}/api/tags/1/").mock(return_value=Response(403))
    result = await tags.paperless_tags_delete(id=1, confirm=True)
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED


@respx.mock
async def test_correspondents_create_not_allowed():
    respx.post(f"{BASE}/api/correspondents/").mock(return_value=Response(401))
    result = await correspondents.paperless_correspondents_create(name="Acme")
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED


@respx.mock
async def test_documents_bulk_update_not_allowed():
    respx.post(f"{BASE}/api/documents/bulk_edit/").mock(return_value=Response(403))
    result = await documents.paperless_documents_bulk_update(
        documentIds="1", operation="add_tag", value=1, dryRun=False, confirm=True
    )
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED


@respx.mock
async def test_ping_not_allowed():
    respx.get(f"{BASE}/api/status/").mock(return_value=Response(401))
    result = await health.paperless_ping()
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED
