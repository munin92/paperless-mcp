"""The Keycloak -> Paperless two-step token exchange (identity.py) and its
wiring into server.get_client() / PaperlessClient's retry-once-on-401.
"""

import asyncio
import base64
import json
import logging
from urllib.parse import parse_qs

import pytest
import respx
from httpx import Response

import paperless_mcp_oidc.server as srv
from paperless_mcp_oidc.client import PaperlessClient
from paperless_mcp_oidc.identity import (
    ID_TOKEN_GRANT_TYPE,
    REQUESTED_TOKEN_TYPE,
    SUBJECT_TOKEN_TYPE,
    KeycloakExchangeError,
    PaperlessLinkError,
    TokenExchanger,
)
from paperless_mcp_oidc.responses import ErrorCodes
from paperless_mcp_oidc.tools import documents

KEYCLOAK_TOKEN_ENDPOINT = "https://keycloak.example.com/realms/home/protocol/openid-connect/token"
PAPERLESS_BASE = "https://paperless.example.com"
HEADLESS_PATH = "/api/auth/headless/app/v1/auth/provider/token"


def _exchanger(**overrides) -> TokenExchanger:
    kwargs = dict(
        token_endpoint=KEYCLOAK_TOKEN_ENDPOINT,
        exchange_client_id="paperless",
        exchange_client_secret="s3cr3t",
        paperless_base_url=PAPERLESS_BASE,
    )
    kwargs.update(overrides)
    return TokenExchanger(**kwargs)


def _mock_keycloak_ok(id_token="id-token-xyz"):
    return respx.post(KEYCLOAK_TOKEN_ENDPOINT).mock(
        return_value=Response(
            200,
            json={"access_token": id_token, "issued_token_type": REQUESTED_TOKEN_TYPE, "token_type": "N_A"},
        )
    )


def _mock_paperless_headless_ok(drf_token="drf-token-abc"):
    return respx.post(f"{PAPERLESS_BASE}{HEADLESS_PATH}").mock(
        return_value=Response(200, json={"status": 200, "meta": {"access_token": drf_token}})
    )


# --- happy path --------------------------------------------------------


@respx.mock
async def test_happy_path_sends_exact_keycloak_form_and_headless_body():
    kc_route = _mock_keycloak_ok(id_token="id-token-xyz")
    hl_route = _mock_paperless_headless_ok(drf_token="drf-token-abc")

    ex = _exchanger()
    drf_token = await ex.get_drf_token(sub="sub-1", access_token="inbound-access-token", username="alice")

    assert drf_token == "drf-token-abc"

    kc_request = kc_route.calls.last.request
    form = parse_qs(kc_request.content.decode())
    assert form == {
        "grant_type": [ID_TOKEN_GRANT_TYPE],
        "subject_token": ["inbound-access-token"],
        "subject_token_type": [SUBJECT_TOKEN_TYPE],
        "requested_token_type": [REQUESTED_TOKEN_TYPE],
        "scope": ["openid"],
    }
    # client_secret_basic
    expected_basic = "Basic " + base64.b64encode(b"paperless:s3cr3t").decode()
    assert kc_request.headers["Authorization"] == expected_basic

    hl_body = json.loads(hl_route.calls.last.request.content)
    assert hl_body == {
        "provider": "keycloak",
        "process": "login",
        "token": {"client_id": "paperless", "id_token": "id-token-xyz"},
    }


@respx.mock
async def test_drf_token_used_as_paperless_authorization():
    _mock_keycloak_ok()
    _mock_paperless_headless_ok(drf_token="drf-token-abc")
    ex = _exchanger()
    drf_token = await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")

    c = PaperlessClient(base_url=PAPERLESS_BASE, token=drf_token)
    tags_route = respx.get(f"{PAPERLESS_BASE}/api/tags/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "previous": None, "results": []})
    )
    await c.get_paginated("/api/tags/")
    assert tags_route.calls.last.request.headers["Authorization"] == "Token drf-token-abc"


# --- caching + concurrency ----------------------------------------------


@respx.mock
async def test_second_call_within_ttl_does_not_re_exchange():
    kc_route = _mock_keycloak_ok()
    hl_route = _mock_paperless_headless_ok()
    ex = _exchanger()

    first = await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")
    second = await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")

    assert first == second
    assert kc_route.call_count == 1
    assert hl_route.call_count == 1


@respx.mock
async def test_concurrent_calls_for_the_same_person_share_one_exchange():
    kc_route = _mock_keycloak_ok()
    hl_route = _mock_paperless_headless_ok()
    ex = _exchanger()

    results = await asyncio.gather(
        *(ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice") for _ in range(5))
    )

    assert all(r == results[0] for r in results)
    assert kc_route.call_count == 1
    assert hl_route.call_count == 1


# --- errors ---------------------------------------------------------------


async def test_keycloak_exchange_error_is_clear_and_paperless_is_never_called():
    # assert_all_called=False: hl_route is registered but deliberately never
    # called — that absence is exactly what this test checks. A plain
    # `@respx.mock(assert_all_called=False)` decorator creates its own router
    # distinct from the module-level `respx.post` calls in the helpers above,
    # so this uses the router the `with` block yields, consistently.
    with respx.mock(assert_all_called=False) as mock:
        kc_route = mock.post(KEYCLOAK_TOKEN_ENDPOINT).mock(
            return_value=Response(400, json={"error": "invalid_token"})
        )
        hl_route = mock.post(f"{PAPERLESS_BASE}{HEADLESS_PATH}")
        ex = _exchanger()

        with pytest.raises(KeycloakExchangeError, match="Keycloak token exchange failed") as excinfo:
            await ex.get_drf_token(sub="sub-1", access_token="super-secret-inbound-token", username="alice")

        assert "super-secret-inbound-token" not in str(excinfo.value)
        assert kc_route.called
        assert not hl_route.called


async def test_keycloak_wrong_issued_token_type_is_rejected():
    with respx.mock(assert_all_called=False) as mock:
        mock.post(KEYCLOAK_TOKEN_ENDPOINT).mock(
            return_value=Response(
                200,
                json={
                    "access_token": "not-an-id-token",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        )
        hl_route = mock.post(f"{PAPERLESS_BASE}{HEADLESS_PATH}")
        ex = _exchanger()

        with pytest.raises(KeycloakExchangeError, match="issued_token_type"):
            await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")

        assert not hl_route.called


@respx.mock
async def test_paperless_pending_flow_response_is_a_link_account_error():
    _mock_keycloak_ok()
    # A pending signup/verification flow: 200 but no meta.access_token.
    respx.post(f"{PAPERLESS_BASE}{HEADLESS_PATH}").mock(
        return_value=Response(200, json={"status": 401, "data": {"flows": [{"id": "signup"}]}})
    )
    ex = _exchanger()

    with pytest.raises(PaperlessLinkError, match="connect Keycloak"):
        await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")


@respx.mock
async def test_paperless_non_200_headless_response_is_a_link_account_error():
    _mock_keycloak_ok()
    respx.post(f"{PAPERLESS_BASE}{HEADLESS_PATH}").mock(return_value=Response(401))
    ex = _exchanger()

    with pytest.raises(PaperlessLinkError, match="connect Keycloak"):
        await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")


# --- host header ------------------------------------------------------------


@respx.mock
async def test_host_header_sent_on_headless_login_when_configured():
    _mock_keycloak_ok()
    hl_route = _mock_paperless_headless_ok()
    ex = _exchanger(host_header="paperless.internal.example.com")

    await ex.get_drf_token(sub="sub-1", access_token="inbound", username="alice")

    assert hl_route.calls.last.request.headers["Host"] == "paperless.internal.example.com"


# --- end-to-end through server.get_client() + tool call --------------------


class _Token:
    def __init__(self, claims, *, token="raw-jwt"):
        self.claims = claims
        self.token = token
        self.subject = claims.get("sub")


@respx.mock
async def test_end_to_end_tool_call_never_sends_a_remote_user_header(monkeypatch):
    _mock_keycloak_ok(id_token="id-token-xyz")
    _mock_paperless_headless_ok(drf_token="drf-token-alice")
    docs_route = respx.get(f"{PAPERLESS_BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "invoice"})
    )

    monkeypatch.setattr(srv, "_exchanger", _exchanger())
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-alice", "preferred_username": "alice"})
    )
    monkeypatch.setattr(documents, "get_client", srv.get_client)

    result = await documents.paperless_documents_get(id=42)

    assert result["ok"] is True
    request = docs_route.calls.last.request
    assert request.headers["Authorization"] == "Token drf-token-alice"
    assert all("remote" not in h.lower() and "preferred-username" not in h.lower() for h in request.headers)


@respx.mock
async def test_401_then_reexchange_then_success(monkeypatch):
    kc_route = _mock_keycloak_ok(id_token="id-token-xyz")
    hl_route = _mock_paperless_headless_ok(drf_token="drf-token-1")
    docs_route = respx.get(f"{PAPERLESS_BASE}/api/documents/42/")
    docs_route.side_effect = [Response(401), Response(200, json={"id": 42, "title": "x"})]

    monkeypatch.setattr(srv, "_exchanger", _exchanger())
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-alice", "preferred_username": "alice"})
    )
    monkeypatch.setattr(documents, "get_client", srv.get_client)

    result = await documents.paperless_documents_get(id=42)

    assert result["ok"] is True
    assert kc_route.call_count == 2  # initial exchange + one forced re-exchange
    assert hl_route.call_count == 2
    assert docs_route.calls.last.request.headers["Authorization"] == "Token drf-token-1"


@respx.mock
async def test_401_twice_is_not_allowed(monkeypatch):
    _mock_keycloak_ok()
    _mock_paperless_headless_ok(drf_token="drf-token-1")
    respx.get(f"{PAPERLESS_BASE}/api/documents/42/").mock(return_value=Response(401))

    monkeypatch.setattr(srv, "_exchanger", _exchanger())
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-alice", "preferred_username": "alice"})
    )
    monkeypatch.setattr(documents, "get_client", srv.get_client)

    result = await documents.paperless_documents_get(id=42)

    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCodes.NOT_ALLOWED


# --- no leaking tokens -------------------------------------------------


@respx.mock
async def test_tokens_never_appear_in_logs_or_tool_results(monkeypatch, caplog):
    _mock_keycloak_ok(id_token="id-token-super-secret")
    _mock_paperless_headless_ok(drf_token="drf-token-super-secret")
    respx.get(f"{PAPERLESS_BASE}/api/documents/42/").mock(
        return_value=Response(200, json={"id": 42, "title": "x"})
    )

    monkeypatch.setattr(srv, "_exchanger", _exchanger())
    monkeypatch.setattr(
        srv,
        "get_access_token",
        lambda: _Token({"sub": "sub-alice", "preferred_username": "alice"}, token="inbound-access-secret"),
    )
    monkeypatch.setattr(documents, "get_client", srv.get_client)

    with caplog.at_level(logging.DEBUG):
        result = await documents.paperless_documents_get(id=42)

    result_text = json.dumps(result)
    for secret in ("id-token-super-secret", "drf-token-super-secret", "inbound-access-secret"):
        assert secret not in caplog.text
        assert secret not in result_text


def test_config_still_refuses_incomplete_oidc_settings(monkeypatch):
    # See test_config.py for the full required-settings matrix; this is a
    # smoke check that EXCHANGE_CLIENT_SECRET specifically is enforced.
    from paperless_mcp_oidc.config import Settings

    monkeypatch.delenv("EXCHANGE_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError, match="EXCHANGE_CLIENT_SECRET"):
        Settings(
            paperless_base_url="https://p.example.com",
            oidc_jwks_uri="https://k/jwks",
            oidc_issuer="https://k",
            oidc_audience="paperless",
            mcp_base_url="https://mcp.example.com",
            oidc_token_endpoint="https://k/token",
            exchange_client_id="paperless",
        )
