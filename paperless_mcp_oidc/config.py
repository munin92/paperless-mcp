import sys
import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the Paperless MCP server.

    AUTH_MODE picks one of two, explicit, non-overlapping identity models:

    - "oidc" (default): every call acts as the calling person. Their verified
      Keycloak access token is exchanged, via Keycloak Standard Token
      Exchange and Paperless-ngx's headless allauth login, into their own
      Paperless DRF API token (see identity.py) — never a remote-user
      header, never a shared token. All OIDC_*, MCP_BASE_URL, and
      EXCHANGE_CLIENT_* settings are required; there is no fallback. An
      incomplete config must refuse to start rather than quietly degrade to
      "everyone shares one identity" — that is exactly the leak this server
      exists to close.
    - "token": behaves like the upstream barryw/PaperlessMCP — one shared
      PAPERLESS_API_TOKEN for everyone. Documented as single-user (README).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_mode: str = "oidc"

    # --- Paperless-ngx -------------------------------------------------
    paperless_base_url: str
    # Sent as the Host header on every Paperless request when set — needed
    # when PAPERLESS_BASE_URL is an in-cluster service URL but Paperless'
    # ALLOWED_HOSTS is the public name.
    paperless_host_header: str = ""
    # Optional browser-facing Paperless URL, used only to build the `url`
    # deep link of paperless_documents_thumbnail_image. Omitted when unset.
    paperless_public_url: str = ""

    # --- token mode ------------------------------------------------------
    paperless_api_token: str = ""

    # --- oidc mode: inbound token verification (all required in that mode) --
    oidc_jwks_uri: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = ""

    # Public address of this server, needed so clients can discover the OAuth
    # metadata and authenticate against the issuer themselves.
    mcp_base_url: str = ""

    # Claim used to label the caller for the per-person outbox directory and
    # debug logging — identity towards Paperless itself now comes from the
    # token exchange below, not from this claim.
    oidc_username_claim: str = "preferred_username"

    # --- oidc mode: Keycloak -> Paperless token exchange (all required) ----
    oidc_token_endpoint: str = ""
    exchange_client_id: str = ""
    exchange_client_secret: str = ""

    # Paperless allauth provider id for the Keycloak connection.
    paperless_oidc_provider_id: str = "keycloak"

    # How long an exchanged Paperless DRF token is cached in memory, per person.
    paperless_token_cache_seconds: float = 3600

    http_timeout_seconds: float = 15
    max_page_size: int = 100

    # Directory paperless_documents_export_to_outbox writes into.
    paperless_outbox_dir: str = "/home/mcp/outbox"

    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @model_validator(mode="after")
    def _validate_mode(self) -> "Settings":
        if self.auth_mode not in ("oidc", "token"):
            raise ValueError(f"AUTH_MODE must be 'oidc' or 'token', got {self.auth_mode!r}")

        if self.auth_mode == "oidc":
            missing = [
                name
                for name, value in (
                    ("OIDC_JWKS_URI", self.oidc_jwks_uri),
                    ("OIDC_ISSUER", self.oidc_issuer),
                    ("OIDC_AUDIENCE", self.oidc_audience),
                    ("MCP_BASE_URL", self.mcp_base_url),
                    ("OIDC_TOKEN_ENDPOINT", self.oidc_token_endpoint),
                    ("EXCHANGE_CLIENT_ID", self.exchange_client_id),
                    ("EXCHANGE_CLIENT_SECRET", self.exchange_client_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "AUTH_MODE=oidc requires "
                    + ", ".join(missing)
                    + " — refusing to start with a shared identity by accident."
                )
            if self.paperless_api_token:
                warnings.warn(
                    "PAPERLESS_API_TOKEN is set but AUTH_MODE=oidc — it is never read "
                    "in this mode; every call uses the caller's own verified identity.",
                    stacklevel=2,
                )
        else:  # token
            if not self.paperless_api_token:
                raise ValueError("AUTH_MODE=token requires PAPERLESS_API_TOKEN")

        return self


try:
    settings = Settings()
except Exception as exc:  # pragma: no cover - exercised via subprocess/manual runs
    print(f"paperless-mcp-oidc: refusing to start — {exc}", file=sys.stderr)
    raise
