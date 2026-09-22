import pytest
import respx
from conftest import async_return
from httpx import Response

from paperless_mcp_oidc.tools import health

BASE = "https://paperless.example.com"


@pytest.fixture(autouse=True)
def _wire_client(monkeypatch, paperless_client):
    monkeypatch.setattr(health, "get_client", async_return(paperless_client))


@respx.mock
async def test_ping_reports_version():
    respx.get(f"{BASE}/api/status/").mock(return_value=Response(200, json={"pngx_version": "2.20.15"}))
    result = await health.paperless_ping()
    assert result["ok"] is True
    assert result["result"] == {"connected": True, "version": "2.20.15"}


@respx.mock
async def test_capabilities_lists_endpoints_and_status():
    respx.get(f"{BASE}/api/status/").mock(return_value=Response(200, json={"pngx_version": "2.20.15"}))
    result = await health.paperless_capabilities()
    assert result["ok"] is True
    assert result["result"]["connected"] is True
    assert result["result"]["version"] == "2.20.15"
    assert result["result"]["endpoints"]["documents"]["search"] == "/api/documents/"
    assert "reprocess" in result["result"]["bulk_edit_methods"]
