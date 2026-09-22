"""Real JWT verification through FastMCP's JWTVerifier — the layer that runs
BEFORE server.get_client() ever sees a claim. Mints RS256 tokens with a test
RSA key (fastmcp ships RSAKeyPair for exactly this) and verifies with the
same issuer/audience config server._build_auth() uses, so this exercises the
actual crypto/claim checks Keycloak's tokens would go through — not just our
business logic on top of them.
"""


import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

ISSUER = "https://keycloak.example.com/realms/x"
AUDIENCE = "paperless-mcp"


@pytest.fixture(scope="module")
def keypair() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture
def verifier(keypair: RSAKeyPair) -> JWTVerifier:
    return JWTVerifier(public_key=keypair.public_key, issuer=ISSUER, audience=AUDIENCE)


async def test_valid_token_is_accepted(keypair, verifier):
    token = keypair.create_token(
        issuer=ISSUER, audience=AUDIENCE, additional_claims={"preferred_username": "alice"}
    )
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.claims["preferred_username"] == "alice"


async def test_wrong_audience_is_rejected(keypair, verifier):
    token = keypair.create_token(issuer=ISSUER, audience="some-other-service")
    assert await verifier.verify_token(token) is None


async def test_wrong_issuer_is_rejected(keypair, verifier):
    token = keypair.create_token(issuer="https://attacker.example.com", audience=AUDIENCE)
    assert await verifier.verify_token(token) is None


async def test_expired_token_is_rejected(keypair, verifier):
    token = keypair.create_token(issuer=ISSUER, audience=AUDIENCE, expires_in_seconds=-10)
    assert await verifier.verify_token(token) is None


async def test_token_signed_by_a_different_key_is_rejected(verifier):
    other_keypair = RSAKeyPair.generate()
    token = other_keypair.create_token(issuer=ISSUER, audience=AUDIENCE)
    assert await verifier.verify_token(token) is None


async def test_garbage_token_is_rejected(verifier):
    assert await verifier.verify_token("not-a-jwt-at-all") is None


async def test_missing_username_claim_still_verifies_but_carries_no_claim(keypair, verifier):
    """The JWT layer only checks signature/issuer/audience/expiry — a missing
    preferred_username claim is caught one layer up, in server.get_client()
    (see test_identity.py::test_missing_claim_fails)."""
    token = keypair.create_token(issuer=ISSUER, audience=AUDIENCE)
    result = await verifier.verify_token(token)
    assert result is not None
    assert "preferred_username" not in (result.claims or {})


def test_build_auth_wires_jwks_uri_issuer_audience_and_base_url(monkeypatch):
    """server._build_auth() must construct JWTVerifier from the OIDC_* + MCP_BASE_URL
    settings — this checks the wiring without a real Keycloak/JWKS endpoint."""
    import paperless_mcp_oidc.server as srv

    provider = srv._build_auth()
    assert provider is not None
    verifier = provider.token_verifier
    assert verifier.jwks_uri == srv.settings.oidc_jwks_uri
    assert verifier.issuer == srv.settings.oidc_issuer
    assert srv.settings.oidc_audience in (
        verifier.audience if isinstance(verifier.audience, list) else [verifier.audience]
    )


def test_build_auth_returns_none_in_token_mode(monkeypatch):
    import paperless_mcp_oidc.server as srv

    monkeypatch.setattr(srv.settings, "auth_mode", "token", raising=False)
    assert srv._build_auth() is None
