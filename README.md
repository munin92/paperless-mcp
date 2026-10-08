# paperless-mcp-oidc

A drop-in replacement for [barryw/PaperlessMCP](https://github.com/barryw/PaperlessMCP)
(v0.6.0) with **one** difference: every call acts as the calling person
(Keycloak/OIDC identity passthrough) instead of a shared Paperless API token.

Same 44 tools (plus the extension `paperless_documents_thumbnail_image`, see
[Extensions](#extensions)), same names, same parameter names/defaults, same result shapes —
existing clients and a gateway allowlist keep working unchanged. See
[Parity](#parity-with-barrywpaperlessmcp) below.

## Why

The upstream server uses one Paperless API token for every caller. In a
household/shared-Paperless setup that means every person (or every agent
acting on their behalf) sees every other person's documents — there is no way
to tell Paperless "this call is really from Alice, not from whoever holds the
token". This server closes that gap: it verifies the caller's own Keycloak JWT
and exchanges it, through Keycloak's own token-exchange support and
Paperless-ngx's headless allauth login, for that person's own Paperless API
token — never a shared token, and never a header Paperless would have to
blindly trust.

## Auth modes

### `AUTH_MODE=oidc` (default)

Every call acts as the calling person, through a two-step token exchange (see
`paperless_mcp_oidc/identity.py`):

1. **Inbound**: FastMCP verifies the caller's Keycloak access token
   (signature, issuer, audience, expiry) against `OIDC_JWKS_URI` /
   `OIDC_ISSUER` / `OIDC_AUDIENCE`.
2. **Keycloak Standard Token Exchange (V2)**: that access token is exchanged,
   as the confidential `EXCHANGE_CLIENT_ID` client
   (`client_secret_basic`, `EXCHANGE_CLIENT_SECRET`), for an OIDC `id_token`
   — `POST OIDC_TOKEN_ENDPOINT` with
   `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`.
3. **Paperless-ngx headless allauth login**: that `id_token` is redeemed at
   `POST {PAPERLESS_BASE_URL}/api/auth/headless/app/v1/auth/provider/token`
   for the person's own persistent Paperless DRF API token.
4. Every Paperless REST call then carries `Authorization: Token <that DRF
   token>` — never a remote-user header, never the inbound Keycloak token.

The resulting DRF token is cached **in memory only**, per verified `sub`, for
`PAPERLESS_TOKEN_CACHE_SECONDS` (default 1h) — most calls don't repeat steps
2–3. On a `401`/`403` from Paperless with a cached token, the server drops it,
redoes the exchange once, and retries the request once; a second `401`/`403`
surfaces as the usual `NOT_ALLOWED` error.

All of `OIDC_JWKS_URI`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `MCP_BASE_URL`,
`OIDC_TOKEN_ENDPOINT`, `EXCHANGE_CLIENT_ID`, and `EXCHANGE_CLIENT_SECRET` are
required — the server refuses to start otherwise (a half-configured identity
check is worse than none, because it looks safe). No `PAPERLESS_API_TOKEN` is
read in this mode; if one happens to be set, the server logs a warning and
ignores it. A missing token, a failed Keycloak exchange, or a Paperless
account that has never been linked to Keycloak all fail the tool call before
Paperless is asked for any data.

**Keycloak setup required:**

- The Paperless OIDC client (`EXCHANGE_CLIENT_ID`, e.g. `paperless`) must be
  **confidential** (has a client secret) and have **"Standard token
  exchange"** enabled (Keycloak 26.x: Client → Advanced tab → Fine grain
  OpenID Connect configuration → Standard token exchange, or Client →
  Permissions → enable "Set custom permissions" then grant `token-exchange`).
- Whatever issues the **inbound** MCP access token needs `paperless` (or your
  `OIDC_AUDIENCE`) present in its `aud` claim — add an audience mapper for
  `EXCHANGE_CLIENT_ID` on that client.
- Every person must **link their Paperless account to Keycloak once**
  (Paperless UI → user menu → My Profile → connect the Keycloak provider)
  before their first call. Until they do, calls fail with a clear
  "connect Keycloak" error rather than silently falling back to anything.

### `AUTH_MODE=token`

Behaves like upstream barryw/PaperlessMCP: one shared `PAPERLESS_API_TOKEN`,
sent as `Authorization: Token <token>`. Documented here as **single-user** —
use it only when there genuinely is one Paperless identity behind this
server (no IdP available). It does not require any `OIDC_*` setting.

## Configuration

| Variable | Mode | Default | Description |
|---|---|---|---|
| `PAPERLESS_BASE_URL` | both | — (required) | Paperless-ngx instance URL |
| `PAPERLESS_HOST_HEADER` | both | *(unset)* | Sent as the `Host` header on every Paperless request — needed when `PAPERLESS_BASE_URL` is an in-cluster service URL but Paperless' `ALLOWED_HOSTS` is the public name |
| `PAPERLESS_PUBLIC_URL` | both | *(unset)* | Browser-facing Paperless URL, used only for the `url` deep link of `paperless_documents_thumbnail_image`; the link is omitted when unset |
| `AUTH_MODE` | both | `oidc` | `oidc` or `token` |
| `OIDC_JWKS_URI` | oidc | — (required) | Keycloak JWKS endpoint (inbound token verification) |
| `OIDC_ISSUER` | oidc | — (required) | Keycloak realm issuer URL (inbound token verification) |
| `OIDC_AUDIENCE` | oidc | — (required) | Expected `aud` claim on the inbound token, e.g. `paperless` |
| `MCP_BASE_URL` | oidc | — (required) | Public address of this server (OAuth metadata discovery) |
| `OIDC_USERNAME_CLAIM` | oidc | `preferred_username` | JWT claim used to label the caller for the per-person outbox dir and debug logging (not for Paperless identity — that comes from the exchange) |
| `OIDC_TOKEN_ENDPOINT` | oidc | — (required) | Keycloak token endpoint, used for the access_token → id_token exchange |
| `EXCHANGE_CLIENT_ID` | oidc | — (required) | The Paperless OIDC client in Keycloak (confidential, Standard token exchange enabled) |
| `EXCHANGE_CLIENT_SECRET` | oidc | — (required) | That client's secret (`client_secret_basic`) |
| `PAPERLESS_OIDC_PROVIDER_ID` | oidc | `keycloak` | Paperless allauth provider id for the Keycloak connection |
| `PAPERLESS_TOKEN_CACHE_SECONDS` | oidc | `3600` | How long an exchanged Paperless DRF token is cached in memory, per person |
| `PAPERLESS_API_TOKEN` | token | — (required in token mode) | Shared Paperless API token |
| `HTTP_TIMEOUT_SECONDS` | both | `15` | Timeout for calls to Paperless and to Keycloak |
| `MAX_PAGE_SIZE` | both | `100` | Upper bound clamp for any `pageSize` a caller requests |
| `PAPERLESS_OUTBOX_DIR` | both | `/home/mcp/outbox` | Directory `paperless_documents_export_to_outbox` writes into |
| `MCP_HOST` / `MCP_PORT` | both | `0.0.0.0` / `8000` | Bind address for the streamable-HTTP transport |

Copy `.env.example` to `.env` and fill in the required values for your mode.

## Running

```bash
uv sync --all-extras   # or: pip install -e ".[dev]"
cp .env.example .env   # fill in your values
paperless-mcp-oidc     # streamable HTTP on :8000/mcp, health at :8000/healthz
```

```bash
docker build -t paperless-mcp-oidc .
docker run --rm -p 8000:8000 --env-file .env paperless-mcp-oidc
```

## Security

**No remote-user header, ever.** Every Paperless call carries
`Authorization: Token <drf-token>` — the person's own token, obtained through
the Keycloak → Paperless exchange (see Auth modes above), never a header
Paperless would have to blindly trust. Unlike the remote-user approach, this
server does **not** depend on Paperless-ngx being unreachable from anything
else — there is no `NetworkPolicy` this server's correctness relies on, since
nothing downstream of it can forge another person's identity by setting a
header. Paperless-ngx's own permission model still decides what an
authenticated user can see, same as if they had logged in directly.

**DRF tokens live in memory only.** The per-person cache (see
`identity.TokenExchanger`) is process memory, never written to disk or a
external store, and never logged. Restarting the server drops it; the next
call for each person simply re-exchanges.

**File tools are confined per person in `oidc` mode.** `paperless_documents_export_to_outbox`
writes into `PAPERLESS_OUTBOX_DIR/<user>-<hash>/`, and `paperless_documents_upload_from_path`
only reads files inside the caller's own outbox (resolved path, so `..` and symlinks cannot
escape; the check runs before the existence check, so it never reveals which other files
exist). This is a deliberate deviation from the upstream, where both tools see the whole
container — fine for one user, a cross-user leak for many. `token` mode keeps the upstream
behaviour.

**Superusers still see everything.** The exchanged DRF token belongs to a
Paperless *user*, and Paperless's own permission model (not this server)
decides what that user can see. A caller whose linked Paperless account is a
superuser sees every document, same as they would logging in directly — this
server does not add per-document access control beyond what Paperless itself
enforces for that user.

**`AUTH_MODE=token` has no per-caller isolation** — it is the same
single-shared-identity model as upstream barryw/PaperlessMCP. Use it only
when that is actually what you want.

## Parity with barryw/PaperlessMCP

All 44 tools, their names, parameter names (including the C# camelCase
originals like `pageSize`, `documentType`, `matchingAlgorithm` — not Python's
`snake_case` convention, since MCP clients call this server by parameter
name), defaults, and JSON result shapes (`{ok, result, meta, warnings}` /
`{ok, error: {code, message, details}, meta}`) mirror the C# reference
1:1 where practical. Notable, deliberately-ported non-obvious behaviours:

- **`MAX_PAGE_SIZE` clamp**: any `pageSize` a caller requests is clamped to
  `[1, MAX_PAGE_SIZE]`; a non-positive configured maximum falls back to 100.
- **Destructive/bulk tools** default to a dry run: `delete`-style tools show
  what would be deleted unless `confirm=true`; bulk tools default
  `dryRun=true` and additionally require `confirm=true` to execute.
- **`paperless_documents_update`**: `correspondent`/`documentType`/`storagePath`
  `= -1` is documented upstream as "clear the field", but the C# reference's
  JSON serializer omits null fields from the request body — so `-1` has
  **the same wire effect as not passing the field at all** (it does not
  actually clear it on Paperless). Ported verbatim rather than silently
  fixed, so this server's requests match what upstream actually sends.
- **`paperless_documents_upload` vs `..._upload_from_path`**: `upload` passes
  the caller's `title` through as-is (or omits it) and lets Paperless derive
  a title from the filename server-side; `upload_from_path` always computes
  and sends the fallback title client-side. Both validate the *effective*
  title against Paperless's real 127-character limit before sending (the
  model allows 128, but Paperless's consumer truncates to `title[:127]` on
  ingestion — silently, so this server rejects instead of corrupting the
  title).
- **`paperless_documents_download` / `export_to_outbox`**: inline base64 is
  capped at 12 KiB — anything larger must go through
  `export_to_outbox`, which streams the file server-side into
  `PAPERLESS_OUTBOX_DIR` (for another MCP server, e.g. a mail client, to pick
  up by path) without the bytes ever passing through the model context.
  Export file names are sanitized to the base name only (no path traversal)
  and, unless an explicit `filename` is given, have the document ID inserted
  for uniqueness.
- **Custom field `select` options**: sent as `{"label": ...}` objects
  (Paperless-ngx 2.14+ shape); updating an existing select field preserves
  each option's `id` by matching on label.

### Not ported 1:1 (and why)

- **Strict-typed JSON deserialization failures.** The C# client fails a
  request outright if Paperless returns a field with the wrong JSON type
  (e.g. a document `id` that isn't a number) and reports `UPSTREAM_ERROR`.
  Python's dynamic typing has no equivalent static deserialization step —
  malformed values pass through as-is. This is a real behavioural gap, but a
  narrow one: Paperless-ngx never actually returns non-conforming JSON in
  practice.
- **Upload-from-path retry/backoff.** The C# tool retries the upload up to 3
  times with exponential backoff on transient I/O or HTTP errors. This
  server does not retry — a failed upload returns an error immediately. Pure
  resilience, doesn't affect the tool's name/parameters/result shape.
- **Legacy `select` custom-field format for Paperless-ngx < 2.14.** The C#
  client auto-detects the server version and falls back to bare-string
  select options for very old Paperless instances. This server always sends
  the 2.14+ object shape (current Paperless-ngx has been on it for a long
  time).
- **Dual-shape `DocumentNoteUser` parsing** (numeric ID vs. object, depending
  on Paperless API version) is a response-parsing nuance with no effect on
  this server's tool inputs/outputs, since notes are passed through as
  received; not specially handled either way.

## Credit

Tool design, naming, parameter shapes, and the non-obvious Paperless-ngx
behaviours documented above are from
[barryw/PaperlessMCP](https://github.com/barryw/PaperlessMCP) by Barry Walker,
licensed MIT. This project reimplements that design in Python with per-caller
identity passthrough as the only functional difference.

## Development

```bash
uv sync --all-extras
uv run pytest -q
uvx ruff check .
```

## License

MIT — see [LICENSE](LICENSE).

## Extensions

- **`paperless_documents_thumbnail_image`** (`{id}`): fetches
  `GET /api/documents/<id>/thumb/` as the calling person and returns an MCP
  content list: one `ImageContent` (base64, the `mimeType` Paperless sends,
  typically `image/webp`) followed by one `TextContent` with compact JSON
  `{"id","title","created","correspondent","document_type","url"}`
  (correspondent/document_type are ids; `url` only when `PAPERLESS_PUBLIC_URL`
  is set). Thumbnails over 512 KB, and 403/404/network failures, return a
  single `TextContent` holding the usual `{ok:false, error}` envelope instead.
