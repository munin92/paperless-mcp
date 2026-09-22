import pytest
import respx
from httpx import Response

from paperless_mcp_oidc.client import PaperlessClient, PaperlessError, PaperlessNotAllowed


def test_needs_a_token():
    with pytest.raises(ValueError, match="needs a token"):
        PaperlessClient(base_url="https://x", token="")


def test_sends_authorization_header():
    c = PaperlessClient(base_url="https://x", token="tok")
    assert c._headers["Authorization"] == "Token tok"


def test_no_remote_user_header_is_ever_sent():
    c = PaperlessClient(base_url="https://x", token="tok")
    assert all("remote" not in h.lower() and "preferred-username" not in h.lower() for h in c._headers)


def test_host_header_applied_when_configured():
    c = PaperlessClient(base_url="https://x", token="tok", host_header="paperless.example.com")
    assert c._headers["Host"] == "paperless.example.com"


def test_host_header_absent_by_default():
    c = PaperlessClient(base_url="https://x", token="tok")
    assert "Host" not in c._headers


def test_accept_header_requests_api_version_10():
    c = PaperlessClient(base_url="https://x", token="tok")
    assert c._headers["Accept"] == "application/json; version=10"


@pytest.mark.parametrize(
    ("requested", "cap", "expected"),
    [
        (None, 100, 100),
        (250, 100, 100),
        (10, 100, 10),
        (0, 100, 1),
        (-5, 100, 1),
        (200, 250, 200),
    ],
)
def test_effective_page_size_clamps(requested, cap, expected):
    c = PaperlessClient(base_url="https://x", token="tok", max_page_size=cap)
    assert c.effective_page_size(requested) == expected


def test_max_page_size_non_positive_falls_back_to_default():
    c = PaperlessClient(base_url="https://x", token="tok", max_page_size=0)
    assert c.effective_page_size(250) == 100


def test_confine_paths_to_outbox_defaults_to_false():
    c = PaperlessClient(base_url="https://x", token="tok")
    assert c.confine_paths_to_outbox is False


@respx.mock
async def test_get_or_none_returns_none_on_404(paperless_client):
    respx.get("https://paperless.example.com/api/tags/999/").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    assert await paperless_client.get_or_none("/api/tags/999/") is None


@respx.mock
async def test_get_or_none_raises_not_allowed_on_403(paperless_client):
    respx.get("https://paperless.example.com/api/tags/1/").mock(return_value=Response(403))
    with pytest.raises(PaperlessNotAllowed):
        await paperless_client.get_or_none("/api/tags/1/")


@respx.mock
async def test_get_raises_paperless_error_on_500(paperless_client):
    respx.get("https://paperless.example.com/api/tags/1/").mock(return_value=Response(500, text="boom"))
    with pytest.raises(PaperlessError):
        await paperless_client.get("/api/tags/1/")


@respx.mock
async def test_delete_returns_true_on_204(paperless_client):
    respx.delete("https://paperless.example.com/api/tags/1/").mock(return_value=Response(204))
    assert await paperless_client.delete("/api/tags/1/") is True


@respx.mock
async def test_delete_raises_not_allowed_on_401(paperless_client):
    respx.delete("https://paperless.example.com/api/tags/1/").mock(return_value=Response(401))
    with pytest.raises(PaperlessNotAllowed):
        await paperless_client.delete("/api/tags/1/")


# --- on_unauthorized retry-once (see identity.py's Keycloak re-exchange) ---


@respx.mock
async def test_a_401_with_no_refresh_hook_raises_immediately():
    c = PaperlessClient(base_url="https://paperless.example.com", token="stale")
    respx.get("https://paperless.example.com/api/tags/1/").mock(return_value=Response(401))
    with pytest.raises(PaperlessNotAllowed):
        await c.get("/api/tags/1/")


@respx.mock
async def test_401_triggers_one_refresh_and_retry_then_succeeds():
    calls = {"n": 0}

    async def refresh() -> str:
        calls["n"] += 1
        return "fresh-token"

    c = PaperlessClient(base_url="https://paperless.example.com", token="stale", on_unauthorized=refresh)
    route = respx.get("https://paperless.example.com/api/tags/1/")
    route.side_effect = [Response(401), Response(200, json={"id": 1})]

    result = await c.get("/api/tags/1/")

    assert result == {"id": 1}
    assert calls["n"] == 1
    assert route.calls.last.request.headers["Authorization"] == "Token fresh-token"


@respx.mock
async def test_401_twice_raises_not_allowed_after_one_retry():
    calls = {"n": 0}

    async def refresh() -> str:
        calls["n"] += 1
        return "still-bad"

    c = PaperlessClient(base_url="https://paperless.example.com", token="stale", on_unauthorized=refresh)
    respx.get("https://paperless.example.com/api/tags/1/").mock(return_value=Response(401))

    with pytest.raises(PaperlessNotAllowed):
        await c.get("/api/tags/1/")

    # Exactly one refresh attempt — not a retry loop.
    assert calls["n"] == 1


@respx.mock
async def test_upload_document_also_retries_once_on_401():
    calls = {"n": 0}

    async def refresh() -> str:
        calls["n"] += 1
        return "fresh-token"

    c = PaperlessClient(base_url="https://paperless.example.com", token="stale", on_unauthorized=refresh)
    route = respx.post("https://paperless.example.com/api/documents/post_document/")
    route.side_effect = [Response(401), Response(200, json="task-1")]

    task_id = await c.upload_document(b"%PDF", "a.pdf")

    assert task_id == "task-1"
    assert calls["n"] == 1
    assert route.calls.last.request.headers["Authorization"] == "Token fresh-token"
