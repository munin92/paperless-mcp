"""AUTH_MODE validation — the case that matters is refusing to start with an
incomplete identity config, not just accepting a complete one."""

import pytest

from paperless_mcp_oidc.config import Settings

REQUIRED_OIDC_KWARGS = {
    "oidc_jwks_uri": "https://k/jwks",
    "oidc_issuer": "https://k",
    "oidc_audience": "paperless",
    "mcp_base_url": "https://mcp.example.com",
    "oidc_token_endpoint": "https://k/token",
    "exchange_client_id": "paperless",
    "exchange_client_secret": "secret",
}

OIDC_ENV_VARS = (
    "OIDC_JWKS_URI",
    "OIDC_ISSUER",
    "OIDC_AUDIENCE",
    "MCP_BASE_URL",
    "OIDC_TOKEN_ENDPOINT",
    "EXCHANGE_CLIENT_ID",
    "EXCHANGE_CLIENT_SECRET",
)


def test_oidc_mode_requires_all_seven_settings(monkeypatch):
    # conftest.py sets these process-wide so collection can import the server
    # module; clear them here so the test exercises Settings() in isolation,
    # not the ambient test environment.
    for var in OIDC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValueError, match="OIDC_JWKS_URI"):
        Settings(paperless_base_url="https://p.example.com")

    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(paperless_base_url="https://p.example.com", oidc_jwks_uri="https://k/jwks")

    with pytest.raises(ValueError, match="MCP_BASE_URL"):
        Settings(
            paperless_base_url="https://p.example.com",
            oidc_jwks_uri="https://k/jwks",
            oidc_issuer="https://k",
            oidc_audience="paperless",
        )

    with pytest.raises(ValueError, match="OIDC_TOKEN_ENDPOINT"):
        Settings(
            paperless_base_url="https://p.example.com",
            oidc_jwks_uri="https://k/jwks",
            oidc_issuer="https://k",
            oidc_audience="paperless",
            mcp_base_url="https://mcp.example.com",
        )

    with pytest.raises(ValueError, match="EXCHANGE_CLIENT_ID"):
        Settings(
            paperless_base_url="https://p.example.com",
            oidc_jwks_uri="https://k/jwks",
            oidc_issuer="https://k",
            oidc_audience="paperless",
            mcp_base_url="https://mcp.example.com",
            oidc_token_endpoint="https://k/token",
        )

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


def test_oidc_mode_with_all_settings_starts():
    s = Settings(paperless_base_url="https://p.example.com", **REQUIRED_OIDC_KWARGS)
    assert s.auth_mode == "oidc"


def test_token_mode_requires_token():
    with pytest.raises(ValueError, match="PAPERLESS_API_TOKEN"):
        Settings(paperless_base_url="https://p.example.com", auth_mode="token")


def test_token_mode_with_token_starts():
    s = Settings(
        paperless_base_url="https://p.example.com", auth_mode="token", paperless_api_token="tok"
    )
    assert s.auth_mode == "token"


def test_unknown_auth_mode_is_rejected():
    with pytest.raises(ValueError, match="AUTH_MODE must be"):
        Settings(paperless_base_url="https://p.example.com", auth_mode="bogus")


def test_token_set_in_oidc_mode_warns_but_still_never_read_at_runtime():
    """PAPERLESS_API_TOKEN alongside AUTH_MODE=oidc is not an error — but a
    warning, since the server never reads it in this mode (see server.get_client)."""
    with pytest.warns(UserWarning, match="never read"):
        s = Settings(
            paperless_base_url="https://p.example.com",
            paperless_api_token="leftover-token",
            **REQUIRED_OIDC_KWARGS,
        )
    assert s.auth_mode == "oidc"
