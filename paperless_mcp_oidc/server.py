import hashlib
import re
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .client import PaperlessClient
from .config import settings
from .identity import KeycloakExchangeError, PaperlessLinkError, TokenExchanger


class NotAuthenticated(RuntimeError):
    """The caller brought no usable, mappable identity."""


def _build_auth():
    """OIDC verification only in AUTH_MODE=oidc; token mode is unprotected
    here (single-user, documented in README) — something else (gateway,
    reverse proxy) must sit in front of it if that matters."""
    if settings.auth_mode != "oidc":
        return None

    from fastmcp.server.auth import RemoteAuthProvider
    from fastmcp.server.auth.providers.jwt import JWTVerifier
    from pydantic import AnyHttpUrl

    # RemoteAuthProvider rather than a bare JWTVerifier: it also serves the
    # OAuth metadata through which a client discovers the issuer itself.
    return RemoteAuthProvider(
        token_verifier=JWTVerifier(
            jwks_uri=settings.oidc_jwks_uri,
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
        ),
        authorization_servers=[AnyHttpUrl(settings.oidc_issuer)],
        base_url=settings.mcp_base_url,
    )


mcp = FastMCP(
    name="Paperless MCP",
    instructions=(
        "Drop-in replacement for barryw/PaperlessMCP. Every call acts as the "
        "calling person's own Paperless-ngx identity — never as a shared account."
    ),
    auth=_build_auth(),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def outbox_segment(username: str) -> str:
    """One outbox directory per person; the hash keeps "a/b" and "a_b" apart."""
    lesbar = re.sub(r"[^A-Za-z0-9._@-]", "_", username).strip(".") or "user"
    return f"{lesbar}-{hashlib.sha256(username.encode()).hexdigest()[:8]}"


def _build_exchanger() -> TokenExchanger | None:
    if settings.auth_mode != "oidc":
        return None
    return TokenExchanger(
        token_endpoint=settings.oidc_token_endpoint,
        exchange_client_id=settings.exchange_client_id,
        exchange_client_secret=settings.exchange_client_secret,
        paperless_base_url=settings.paperless_base_url,
        paperless_oidc_provider_id=settings.paperless_oidc_provider_id,
        host_header=settings.paperless_host_header,
        cache_seconds=settings.paperless_token_cache_seconds,
        http_timeout=settings.http_timeout_seconds,
    )


_exchanger = _build_exchanger()


async def get_client() -> PaperlessClient:
    """Resolves the calling person's Paperless identity for this request.

    AUTH_MODE=token: always the shared PAPERLESS_API_TOKEN.

    AUTH_MODE=oidc: the calling person's verified Keycloak access token
    (never the token itself, only a Paperless DRF token derived from it via
    identity.TokenExchanger) — see identity.py for the two-step exchange.
    No fallback: a missing token, a failed Keycloak exchange, or an
    unlinked Paperless account raises instead of quietly using some other
    identity or the shared token — see README Security section. Paperless
    is never called for data before a successful exchange.
    """
    if settings.auth_mode == "token":
        return PaperlessClient(
            base_url=settings.paperless_base_url,
            token=settings.paperless_api_token,
            host_header=settings.paperless_host_header,
            timeout=settings.http_timeout_seconds,
            outbox_dir=settings.paperless_outbox_dir,
            max_page_size=settings.max_page_size,
        )

    token = get_access_token()
    if token is None:
        raise NotAuthenticated("No valid token — please sign in.")

    sub = token.subject or token.claims.get("sub")
    if not sub:
        raise NotAuthenticated(
            "The token is missing a 'sub' claim; without it you cannot be identified."
        )

    username = token.claims.get(settings.oidc_username_claim) or sub
    access_token = token.token

    async def _refresh() -> str:
        return await _exchanger.get_drf_token(
            sub=sub, access_token=access_token, username=username, force=True
        )

    try:
        drf_token = await _exchanger.get_drf_token(sub=sub, access_token=access_token, username=username)
    except (KeycloakExchangeError, PaperlessLinkError) as exc:
        raise NotAuthenticated(str(exc)) from exc

    return PaperlessClient(
        base_url=settings.paperless_base_url,
        token=drf_token,
        host_header=settings.paperless_host_header,
        timeout=settings.http_timeout_seconds,
        outbox_dir=str(Path(settings.paperless_outbox_dir) / outbox_segment(username)),
        max_page_size=settings.max_page_size,
        confine_paths_to_outbox=True,
        on_unauthorized=_refresh,
    )


# Import tools (registers them on mcp via @mcp.tool decorator).
from . import tools  # noqa: F401, E402


def main():
    import uvicorn

    app = mcp.http_app(path="/mcp")
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
