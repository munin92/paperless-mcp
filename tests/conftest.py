"""Sets required env vars before any paperless_mcp_oidc import happens at
collection time — Settings() refuses to start otherwise (see config.py)."""

import os

os.environ.setdefault("PAPERLESS_BASE_URL", "https://paperless.example.com")
os.environ.setdefault("OIDC_JWKS_URI", "https://keycloak.example.com/realms/x/protocol/openid-connect/certs")
os.environ.setdefault("OIDC_ISSUER", "https://keycloak.example.com/realms/x")
os.environ.setdefault("OIDC_AUDIENCE", "paperless-mcp")
os.environ.setdefault("MCP_BASE_URL", "https://paperless-mcp.example.com")
os.environ.setdefault("OIDC_TOKEN_ENDPOINT", "https://keycloak.example.com/realms/x/protocol/openid-connect/token")
os.environ.setdefault("EXCHANGE_CLIENT_ID", "paperless")
os.environ.setdefault("EXCHANGE_CLIENT_SECRET", "test-exchange-secret")

import pytest

from paperless_mcp_oidc.client import PaperlessClient


@pytest.fixture
def paperless_client() -> PaperlessClient:
    return PaperlessClient(base_url="https://paperless.example.com", token="drf-token-bob")


def async_return(value):
    """Wraps a plain value as an async, no-arg callable — for monkeypatching
    `get_client` (async since the oidc token exchange) with a fixed test
    double without needing an AsyncMock per call site."""

    async def _inner():
        return value

    return _inner
