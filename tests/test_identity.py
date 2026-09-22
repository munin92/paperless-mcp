"""Identity passthrough: every call acts as the calling person, never a shared
account, and never via a Paperless remote-user header — see identity.py for
the Keycloak -> Paperless DRF-token exchange this now goes through. The case
that matters is not "it works" but "it does not silently fall back" — see
server.get_client docstring.
"""

import pytest

import paperless_mcp_oidc.server as srv
from paperless_mcp_oidc.identity import KeycloakExchangeError, PaperlessLinkError


class _Token:
    def __init__(self, claims, *, token="raw-jwt", subject=None):
        self.claims = claims
        self.token = token
        self.subject = subject if subject is not None else claims.get("sub")


class _FakeExchanger:
    """Records calls instead of talking to Keycloak/Paperless — the exchange
    HTTP flow itself is covered end-to-end in test_token_exchange.py."""

    def __init__(self, *, drf_token="drf-token-abc", error: Exception | None = None):
        self.drf_token = drf_token
        self.error = error
        self.calls: list[dict] = []

    async def get_drf_token(self, *, sub, access_token, username="", force=False):
        self.calls.append(
            {"sub": sub, "access_token": access_token, "username": username, "force": force}
        )
        if self.error is not None:
            raise self.error
        return self.drf_token


def test_oidc_mode_is_the_default():
    assert srv.settings.auth_mode == "oidc"


async def test_no_token_no_fallback(monkeypatch):
    monkeypatch.setattr(srv, "get_access_token", lambda: None)
    with pytest.raises(srv.NotAuthenticated, match="sign in"):
        await srv.get_client()


async def test_missing_sub_fails(monkeypatch):
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"preferred_username": "alice"}, subject=None)
    )
    with pytest.raises(srv.NotAuthenticated, match="sub"):
        await srv.get_client()


async def test_valid_token_acts_as_that_person_via_the_exchange(monkeypatch):
    fake = _FakeExchanger(drf_token="drf-token-alice")
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(
        srv,
        "get_access_token",
        lambda: _Token({"preferred_username": "alice", "sub": "sub-alice"}, token="jwt-alice"),
    )

    c = await srv.get_client()

    assert c._headers["Authorization"] == "Token drf-token-alice"
    assert all("remote" not in h.lower() for h in c._headers)
    assert fake.calls == [
        {"sub": "sub-alice", "access_token": "jwt-alice", "username": "alice", "force": False}
    ]


async def test_exchange_is_keyed_by_sub_not_username(monkeypatch):
    fake = _FakeExchanger()
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-123"}, token="jwt")
    )

    await srv.get_client()

    assert fake.calls[0]["sub"] == "sub-123"
    # No username claim present -> falls back to sub for outbox labeling.
    assert fake.calls[0]["username"] == "sub-123"


async def test_custom_username_claim_is_honoured_for_outbox_labeling(monkeypatch):
    fake = _FakeExchanger()
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(srv.settings, "oidc_username_claim", "email", raising=False)
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-1", "email": "alice@example.com"})
    )

    await srv.get_client()

    assert fake.calls[0]["username"] == "alice@example.com"


async def test_keycloak_exchange_failure_raises_not_authenticated(monkeypatch):
    fake = _FakeExchanger(error=KeycloakExchangeError("Keycloak token exchange failed: HTTP 400"))
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(srv, "get_access_token", lambda: _Token({"sub": "sub-1"}, token="jwt-secret-value"))

    with pytest.raises(srv.NotAuthenticated, match="Keycloak token exchange failed") as excinfo:
        await srv.get_client()

    assert "jwt-secret-value" not in str(excinfo.value)


async def test_paperless_link_failure_raises_not_authenticated(monkeypatch):
    fake = _FakeExchanger(
        error=PaperlessLinkError(
            "Paperless has not linked this person's Keycloak identity yet. Sign in to "
            "Paperless once, then under My profile connect Keycloak, and retry."
        )
    )
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(srv, "get_access_token", lambda: _Token({"sub": "sub-1"}, token="jwt-secret-value"))

    with pytest.raises(srv.NotAuthenticated, match="connect Keycloak") as excinfo:
        await srv.get_client()

    assert "jwt-secret-value" not in str(excinfo.value)


async def test_token_mode_never_calls_get_access_token(monkeypatch):
    """AUTH_MODE=token must never touch the OIDC verification path — it is a
    single-user, no-IdP fallback documented in the README."""
    monkeypatch.setattr(srv.settings, "auth_mode", "token", raising=False)
    monkeypatch.setattr(srv.settings, "paperless_api_token", "shared-token", raising=False)

    def boom():
        raise AssertionError("token mode must not call get_access_token()")

    monkeypatch.setattr(srv, "get_access_token", boom)

    c = await srv.get_client()
    assert c._headers["Authorization"] == "Token shared-token"


async def test_oidc_mode_never_reads_the_shared_token(monkeypatch):
    fake = _FakeExchanger(drf_token="drf-token-alice")
    monkeypatch.setattr(srv, "_exchanger", fake)
    monkeypatch.setattr(srv.settings, "paperless_api_token", "leftover-shared-token", raising=False)
    monkeypatch.setattr(
        srv, "get_access_token", lambda: _Token({"sub": "sub-1", "preferred_username": "alice"})
    )

    c = await srv.get_client()

    assert c._headers["Authorization"] == "Token drf-token-alice"
