"""File tools in multi-user mode: one outbox per person, and upload_from_path
may only read from the caller's own outbox — otherwise Bob could upload the
invoice Alice just exported."""

import pytest
import respx
from conftest import async_return
from httpx import Response

import paperless_mcp_oidc.server as srv
from paperless_mcp_oidc.client import PaperlessClient
from paperless_mcp_oidc.responses import ErrorCodes
from paperless_mcp_oidc.tools import documents

BASE = "https://paperless.example.com"


class _Token:
    def __init__(self, claims, *, token="raw-jwt"):
        self.claims = claims
        self.token = token
        self.subject = claims.get("sub")


class _FakeExchanger:
    def __init__(self):
        self.calls = 0

    async def get_drf_token(self, *, sub, access_token, username="", force=False):
        self.calls += 1
        return f"drf-token-{username or sub}"


async def _als(monkeypatch, name):
    monkeypatch.setattr(srv, "_exchanger", _FakeExchanger())
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": f"sub-{name}", "preferred_username": name})
    )
    return await srv.get_client()


async def test_every_person_gets_an_own_outbox(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.settings, "paperless_outbox_dir", str(tmp_path), raising=False)
    bob = (await _als(monkeypatch, "bob")).outbox_dir
    alice = (await _als(monkeypatch, "alice")).outbox_dir
    assert bob != alice
    assert bob.startswith(str(tmp_path)) and alice.startswith(str(tmp_path))


async def test_a_hostile_username_cannot_leave_the_outbox_root(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.settings, "paperless_outbox_dir", str(tmp_path), raising=False)
    ziel = (await _als(monkeypatch, "../../etc")).outbox_dir
    assert (tmp_path / ziel.rsplit("/", 1)[1]).resolve().parent == tmp_path.resolve()


def test_similar_names_do_not_share_an_outbox():
    assert srv.outbox_segment("a/b") != srv.outbox_segment("a_b")


def _client(outbox):
    return PaperlessClient(
        base_url=BASE, token="drf-token-bob", outbox_dir=str(outbox), confine_paths_to_outbox=True
    )


@pytest.mark.parametrize("fremd", ["alice/rechnung.pdf", "../alice/rechnung.pdf"])
async def test_upload_from_path_refuses_another_persons_outbox(tmp_path, monkeypatch, fremd):
    eigene, fremde = tmp_path / "bob", tmp_path / "alice"
    eigene.mkdir(), fremde.mkdir()
    (fremde / "rechnung.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(documents, "get_client", async_return(_client(eigene)))
    ziel = (eigene / fremd) if fremd.startswith("..") else (tmp_path / fremd)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{BASE}/api/documents/post_document/")
        result = await documents.paperless_documents_upload_from_path(filePath=str(ziel))
    assert result["error"]["code"] == ErrorCodes.VALIDATION
    assert not route.called


async def test_a_symlink_out_of_the_own_outbox_is_refused(tmp_path, monkeypatch):
    eigene = tmp_path / "bob"
    eigene.mkdir()
    (tmp_path / "geheim.pdf").write_bytes(b"%PDF")
    (eigene / "link.pdf").symlink_to(tmp_path / "geheim.pdf")
    monkeypatch.setattr(documents, "get_client", async_return(_client(eigene)))
    result = await documents.paperless_documents_upload_from_path(filePath=str(eigene / "link.pdf"))
    assert result["error"]["code"] == ErrorCodes.VALIDATION


async def test_a_foreign_file_does_not_even_reveal_its_existence(tmp_path, monkeypatch):
    eigene = tmp_path / "bob"
    eigene.mkdir()
    monkeypatch.setattr(documents, "get_client", async_return(_client(eigene)))
    da = await documents.paperless_documents_upload_from_path(filePath="/etc/hostname")
    weg = await documents.paperless_documents_upload_from_path(filePath="/no/such/file")
    assert da["error"]["code"] == weg["error"]["code"] == ErrorCodes.VALIDATION


async def test_own_outbox_file_is_uploaded(tmp_path, monkeypatch):
    eigene = tmp_path / "bob"
    eigene.mkdir()
    (eigene / "scan.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(documents, "get_client", async_return(_client(eigene)))
    with respx.mock() as mock:
        route = mock.post(f"{BASE}/api/documents/post_document/").mock(
            return_value=Response(200, json="task-1")
        )
        result = await documents.paperless_documents_upload_from_path(filePath=str(eigene / "scan.pdf"))
    assert route.called, result
    assert route.calls[0].request.headers["Authorization"] == "Token drf-token-bob"
