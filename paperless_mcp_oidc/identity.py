"""Keycloak -> Paperless-ngx identity: two-step token exchange.

AUTH_MODE=oidc callers bring a Keycloak access token, already verified
(signature, issuer, audience, expiry) by FastMCP's RemoteAuthProvider/
JWTVerifier before anything here runs (see server._build_auth()). Paperless
does not accept that token, and this server no longer sends a remote-user
header (see README). Instead, each verified caller is exchanged into their
own Paperless DRF API token:

1. Keycloak Standard Token Exchange (V2): the inbound access token is
   exchanged for an OIDC id_token, as the confidential EXCHANGE_CLIENT_ID
   client (client_secret_basic).
2. Paperless-ngx headless allauth login: that id_token is redeemed at
   /api/auth/headless/app/v1/auth/provider/token for a persistent DRF token
   (Paperless's DrfTokenStrategy -> Token.objects.get_or_create). This step
   fails if the person has never linked their Paperless account to Keycloak.

The resulting DRF token is cached in memory only, keyed by the verified
token's `sub`, for PAPERLESS_TOKEN_CACHE_SECONDS. A per-`sub` asyncio.Lock
ensures concurrent calls for the same person share one exchange rather than
each racing Keycloak/Paperless independently.

Nothing in this module ever logs or returns a token value.
"""

from __future__ import annotations

import logging
import time
from asyncio import Lock
from collections import defaultdict
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

ID_TOKEN_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"

PAPERLESS_HEADLESS_LOGIN_PATH = "/api/auth/headless/app/v1/auth/provider/token"

LINK_ACCOUNT_MESSAGE = (
    "Paperless has not linked this person's Keycloak identity yet. Sign in to "
    "Paperless once, then under My profile connect Keycloak, and retry."
)


class KeycloakExchangeError(RuntimeError):
    """Keycloak token exchange (access token -> id token) failed."""


class PaperlessLinkError(RuntimeError):
    """Paperless headless allauth login (id token -> DRF token) failed —
    typically because the person has never linked their Paperless account to
    Keycloak, or the login is stuck in a pending flow (e.g. signup/verify)."""


@dataclass
class _CacheEntry:
    drf_token: str
    expires_at: float


class TokenExchanger:
    """Exchanges a verified Keycloak access token for a Paperless DRF token,
    caching the result per `sub` and de-duplicating concurrent exchanges for
    the same person via one asyncio.Lock per sub."""

    def __init__(
        self,
        *,
        token_endpoint: str,
        exchange_client_id: str,
        exchange_client_secret: str,
        paperless_base_url: str,
        paperless_oidc_provider_id: str = "keycloak",
        host_header: str = "",
        cache_seconds: float = 3600,
        http_timeout: float = 15,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_endpoint = token_endpoint
        self._exchange_client_id = exchange_client_id
        self._exchange_client_secret = exchange_client_secret
        self._paperless_base_url = paperless_base_url.rstrip("/")
        self._provider_id = paperless_oidc_provider_id
        self._host_header = host_header
        self._cache_seconds = cache_seconds
        self._timeout = http_timeout
        self._http = http_client

        self._cache: dict[str, _CacheEntry] = {}
        self._locks: dict[str, Lock] = defaultdict(Lock)

    def _client(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=self._timeout)

    async def _maybe_close(self, client: httpx.AsyncClient) -> None:
        if self._http is None:
            await client.aclose()

    async def _exchange_id_token(self, access_token: str) -> str:
        """Step 1: Keycloak Standard Token Exchange, access_token -> id_token."""
        data = {
            "grant_type": ID_TOKEN_GRANT_TYPE,
            "subject_token": access_token,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "requested_token_type": REQUESTED_TOKEN_TYPE,
            "scope": "openid",
        }
        client = self._client()
        try:
            resp = await client.post(
                self._token_endpoint,
                data=data,
                auth=(self._exchange_client_id, self._exchange_client_secret),
            )
        finally:
            await self._maybe_close(client)

        if resp.status_code >= 400:
            raise KeycloakExchangeError(f"Keycloak token exchange failed: HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise KeycloakExchangeError(
                "Keycloak token exchange failed: response was not JSON"
            ) from exc

        if body.get("error"):
            raise KeycloakExchangeError(f"Keycloak token exchange failed: {body['error']}")

        issued_type = body.get("issued_token_type")
        if issued_type != REQUESTED_TOKEN_TYPE:
            raise KeycloakExchangeError(
                f"Keycloak token exchange failed: unexpected issued_token_type {issued_type!r}"
            )

        id_token = body.get("access_token")
        if not id_token:
            raise KeycloakExchangeError("Keycloak token exchange failed: response had no access_token")

        return id_token

    async def _login_to_paperless(self, id_token: str) -> str:
        """Step 2: Paperless headless allauth login, id_token -> DRF token."""
        client = self._client()
        headers = {"Host": self._host_header} if self._host_header else None
        try:
            resp = await client.post(
                f"{self._paperless_base_url}{PAPERLESS_HEADLESS_LOGIN_PATH}",
                json={
                    "provider": self._provider_id,
                    "process": "login",
                    "token": {"client_id": self._exchange_client_id, "id_token": id_token},
                },
                headers=headers,
            )
        finally:
            await self._maybe_close(client)

        if resp.status_code != 200:
            raise PaperlessLinkError(LINK_ACCOUNT_MESSAGE)

        try:
            body = resp.json()
        except ValueError as exc:
            raise PaperlessLinkError(LINK_ACCOUNT_MESSAGE) from exc

        drf_token = (body.get("meta") or {}).get("access_token")
        if not drf_token:
            # e.g. a pending "flows" response (signup/verification) — no
            # meta.access_token means the account isn't linked yet.
            raise PaperlessLinkError(LINK_ACCOUNT_MESSAGE)

        return drf_token

    async def get_drf_token(
        self, *, sub: str, access_token: str, username: str = "", force: bool = False
    ) -> str:
        """Returns a Paperless DRF token for the person identified by `sub`,
        from cache when possible. `username` is only used for the debug log
        line below — never the token value itself."""
        lock = self._locks[sub]
        async with lock:
            if force:
                # Drop the stale token first: even if the re-exchange below
                # fails, the next call must not reuse the token that just
                # got a 401 from Paperless.
                self._cache.pop(sub, None)
            else:
                cached = self._cache.get(sub)
                if cached and cached.expires_at > time.monotonic():
                    return cached.drf_token

            id_token = await self._exchange_id_token(access_token)
            drf_token = await self._login_to_paperless(id_token)

            self._cache[sub] = _CacheEntry(
                drf_token=drf_token, expires_at=time.monotonic() + self._cache_seconds
            )
            logger.debug("paperless token exchange succeeded for user=%s", username or sub)
            return drf_token

    def invalidate(self, sub: str) -> None:
        self._cache.pop(sub, None)
