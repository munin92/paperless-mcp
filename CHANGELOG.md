# Changelog

## 0.1.1 (2026-09-22)

### Fixes

* Run as the numeric UID 10001 instead of the user name `mcp`: Kubernetes rejects `runAsNonRoot` when the image sets a non-numeric user ("cannot verify user is non-root").

## 0.1.0 (2026-09-22)

### Features

* Drop-in replacement for [barryw/PaperlessMCP](https://github.com/barryw/PaperlessMCP) v0.6.0 — same 44 tools, same tool names, same parameter names/defaults and result shapes.
* `AUTH_MODE=oidc` (default): every call acts as the calling person. The caller's verified Keycloak access token is exchanged — via Keycloak Standard Token Exchange and Paperless-ngx's headless allauth login — for that person's own Paperless DRF API token, cached in memory per person. Never a remote-user header, never a shared token.
* `oidc` mode confines file tools per person: own outbox subdirectory, `upload_from_path` only inside it (deliberate deviation from upstream).
* `AUTH_MODE=token`: single-user fallback matching upstream's shared `PAPERLESS_API_TOKEN` behaviour for people without an IdP.
* Full document tools: search, get, download, preview, thumbnail, upload (base64 and from-path), update, delete, bulk_update, reprocess, export_to_outbox (shared outbox directory, no bytes through the model context).
* Tags, correspondents, document types, storage paths: list/get/create/update/delete/bulk_delete, each with dry-run + `confirm=true` semantics on destructive operations.
* Custom fields: list/get/create/update/delete/assign, including the select-option and documentlink value handling.
* `paperless_ping` / `paperless_capabilities` health and capability discovery tools.
* Streamable HTTP transport on port 8000 at `/mcp`, plus `/healthz`.
