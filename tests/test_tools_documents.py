"""Document tool request-shape tests, ported from
PaperlessMCP.Tests/Tools/DocumentToolsTests.cs (method/path/query/body, not
the C# test names 1:1)."""

import base64
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from conftest import async_return
from httpx import Response

from paperless_mcp_oidc.responses import ErrorCodes
from paperless_mcp_oidc.tools import documents

BASE = "https://paperless.example.com"


def _page(results=None, count=None):
    results = results or []
    return {
        "count": count if count is not None else len(results),
        "next": None,
        "previous": None,
        "results": results,
    }


@pytest.fixture(autouse=True)
def _client(monkeypatch, paperless_client):
    monkeypatch.setattr(documents, "get_client", async_return(paperless_client))
    return paperless_client


@respx.mock
async def test_search_with_query_returns_results():
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([{"id": i, "title": f"doc{i}"} for i in range(5)]))
    )
    result = await documents.paperless_documents_search(query="invoice")
    assert result["ok"] is True
    assert len(result["result"]) == 5
    assert result["meta"]["total"] == 5


@respx.mock
async def test_search_with_pagination_includes_metadata():
    respx.get(f"{BASE}/api/documents/").mock(return_value=Response(200, json=_page(count=50)))
    result = await documents.paperless_documents_search(page=2, pageSize=10)
    assert result["meta"]["page"] == 2
    assert result["meta"]["page_size"] == 10


@respx.mock
async def test_search_with_correspondent_builds_expected_query():
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([{"id": 42, "title": "x"}]))
    )
    result = await documents.paperless_documents_search(correspondent=17)
    request = route.calls.last.request
    qs = parse_qs(urlparse(str(request.url)).query)
    assert qs["correspondent__id"] == ["17"]
    assert qs["page"] == ["1"]
    assert qs["page_size"] == ["25"]
    assert result["ok"] is True


@respx.mock
async def test_search_forwards_custom_field_query_verbatim():
    route = respx.get(f"{BASE}/api/documents/").mock(return_value=Response(200, json=_page(count=3)))
    cfq = '["Invoice Number","icontains","INV-2024"]'
    result = await documents.paperless_documents_search(customFieldQuery=cfq)
    qs = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert qs["custom_field_query"] == [cfq]
    assert result["ok"] is True


@respx.mock
async def test_search_repeats_tags_and_tags_exclude():
    route = respx.get(f"{BASE}/api/documents/").mock(return_value=Response(200, json=_page()))
    await documents.paperless_documents_search(tags="1,2", tagsExclude="3")
    qs = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert qs["tags__id__in"] == ["1", "2"]
    assert qs["tags__id__none"] == ["3"]


@respx.mock
async def test_get_returns_document():
    respx.get(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42, "title": "x"}))
    result = await documents.paperless_documents_get(id=42)
    assert result["ok"] is True
    assert result["result"]["id"] == 42


@respx.mock
async def test_get_not_found_returns_not_found_error():
    respx.get(f"{BASE}/api/documents/999/").mock(return_value=Response(404))
    result = await documents.paperless_documents_get(id=999)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.NOT_FOUND


@respx.mock
async def test_download_without_base64_returns_urls_only():
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x", "original_file_name": "x.pdf"})
    )
    result = await documents.paperless_documents_download(id=42)
    assert result["ok"] is True
    assert result["result"]["download_url"] == f"{BASE}/api/documents/42/download/"
    assert "content_base64" not in result["result"]


@respx.mock
async def test_download_with_base64_inlines_small_file():
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x", "original_file_name": "x.pdf"})
    )
    respx.get(f"{BASE}/api/documents/42/download/").mock(
        return_value=Response(200, content=b"tiny-file", headers={"content-type": "application/pdf"})
    )
    result = await documents.paperless_documents_download(id=42, returnBase64=True)
    assert result["ok"] is True
    assert base64.b64decode(result["result"]["content_base64"]) == b"tiny-file"
    assert result["result"]["mime_type"] == "application/pdf"


@respx.mock
async def test_download_with_base64_rejects_oversized_file():
    respx.get(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42, "title": "x"}))
    respx.get(f"{BASE}/api/documents/42/download/").mock(
        return_value=Response(200, content=b"x" * (13 * 1024))
    )
    result = await documents.paperless_documents_download(id=42, returnBase64=True)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


@respx.mock
async def test_upload_rejects_invalid_base64():
    result = await documents.paperless_documents_upload(fileContent="not-base64!!", fileName="a.pdf")
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


@respx.mock
async def test_upload_sends_multipart_and_returns_task_id():
    route = respx.post(f"{BASE}/api/documents/post_document/").mock(
        return_value=Response(200, text='"11111111-2222-3333-4444-555555555555"')
    )
    content = base64.b64encode(b"hello world").decode()
    result = await documents.paperless_documents_upload(
        fileContent=content, fileName="hello.txt", title="Hello"
    )
    assert result["ok"] is True
    assert result["result"]["task_id"] == "11111111-2222-3333-4444-555555555555"
    assert route.calls.call_count == 1


@respx.mock
async def test_upload_rejects_over_long_title():
    content = base64.b64encode(b"hi").decode()
    result = await documents.paperless_documents_upload(
        fileContent=content, fileName="a.pdf", title="x" * 128
    )
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


async def test_upload_from_path_rejects_relative_path():
    result = await documents.paperless_documents_upload_from_path(filePath="relative/path.pdf")
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


async def test_upload_from_path_rejects_missing_file(tmp_path, paperless_client, monkeypatch):
    monkeypatch.setattr(paperless_client, "outbox_dir", str(tmp_path))
    result = await documents.paperless_documents_upload_from_path(filePath=str(tmp_path / "missing.pdf"))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.NOT_FOUND


@respx.mock
async def test_update_strips_content_unless_include_content(tmp_path):
    respx.patch(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "New", "content": "the ocr text"})
    )
    result = await documents.paperless_documents_update(id=42, title="New")
    assert result["result"]["content"] == ""

    result = await documents.paperless_documents_update(id=42, title="New", includeContent=True)
    assert result["result"]["content"] == "the ocr text"


@respx.mock
async def test_update_minus_one_omits_field_like_csharp_does():
    """Verbatim-ported quirk: correspondent=-1 has the SAME wire effect as
    omitting it entirely (see documents.py comment) — this pins that down."""
    route = respx.patch(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42}))
    await documents.paperless_documents_update(id=42, correspondent=-1)
    import json

    body = json.loads(route.calls.last.request.content)
    assert "correspondent" not in body


async def test_delete_without_confirm_is_a_dry_run():
    pass  # covered in test_tools_dry_run.py


@respx.mock
async def test_delete_dry_run_shows_confirmation_required():
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x", "original_file_name": "x.pdf"})
    )
    result = await documents.paperless_documents_delete(id=42)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.CONFIRMATION_REQUIRED
    assert result["error"]["details"]["document_id"] == 42


@respx.mock
async def test_delete_with_confirm_deletes():
    respx.delete(f"{BASE}/api/documents/42/").mock(return_value=Response(204))
    result = await documents.paperless_documents_delete(id=42, confirm=True)
    assert result["ok"] is True
    assert result["result"] == {"deleted": True, "document_id": 42}


async def test_bulk_update_rejects_invalid_operation():
    result = await documents.paperless_documents_bulk_update(documentIds="1,2", operation="nonsense")
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.VALIDATION


async def test_bulk_update_defaults_to_dry_run():
    result = await documents.paperless_documents_bulk_update(documentIds="1,2", operation="add_tag", value=5)
    assert result["ok"] is True
    assert result["result"]["executed"] is False
    assert result["result"]["affected_ids"] == [1, 2]


@respx.mock
async def test_bulk_update_executes_with_confirm():
    route = respx.post(f"{BASE}/api/documents/bulk_edit/").mock(return_value=Response(200, json={}))
    result = await documents.paperless_documents_bulk_update(
        documentIds="1,2", operation="add_tag", value=5, dryRun=False, confirm=True
    )
    assert result["ok"] is True
    assert result["result"]["executed"] is True
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"documents": [1, 2], "method": "add_tag", "parameters": {"tag": 5}}


@respx.mock
async def test_reprocess_dry_run_then_confirm():
    respx.get(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42, "title": "x"}))
    dry = await documents.paperless_documents_reprocess(id=42)
    assert dry["error"]["code"] == ErrorCodes.CONFIRMATION_REQUIRED

    respx.post(f"{BASE}/api/documents/bulk_edit/").mock(return_value=Response(200, json={}))
    done = await documents.paperless_documents_reprocess(id=42, confirm=True)
    assert done["ok"] is True
    assert done["result"]["document_id"] == 42


@respx.mock
async def test_export_to_outbox_writes_file(tmp_path, monkeypatch):
    paperless_client = await documents.get_client()
    monkeypatch.setattr(paperless_client, "outbox_dir", str(tmp_path))

    doc_json = {
        "id": 42,
        "title": "x",
        "original_file_name": "invoice.pdf",
        "archived_file_name": None,
    }
    respx.get(f"{BASE}/api/documents/42/").mock(
        return_value=Response(200, json=doc_json)
    )
    respx.get(f"{BASE}/api/documents/42/download/").mock(
        return_value=Response(200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"})
    )

    result = await documents.paperless_documents_export_to_outbox(id=42)
    assert result["ok"] is True
    path = result["result"]["path"]
    assert path.startswith(str(tmp_path))
    assert "42" in result["result"]["filename"]
    with open(path, "rb") as f:
        assert f.read() == b"%PDF-1.4 fake"


@respx.mock
async def test_export_to_outbox_rejects_path_traversal_in_filename(tmp_path, monkeypatch):
    paperless_client = await documents.get_client()
    monkeypatch.setattr(paperless_client, "outbox_dir", str(tmp_path))

    respx.get(f"{BASE}/api/documents/42/").mock(return_value=Response(200, json={"id": 42, "title": "x"}))
    respx.get(f"{BASE}/api/documents/42/download/").mock(return_value=Response(200, content=b"data"))

    result = await documents.paperless_documents_export_to_outbox(id=42, filename="../../etc/passwd")
    assert result["ok"] is True
    assert result["result"]["path"] == str(tmp_path / "passwd")
