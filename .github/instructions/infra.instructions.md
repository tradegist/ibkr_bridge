---
applyTo: "infra/**,docker-compose*.yml"
---

# `infra/` — Infrastructure backbone

Caddy reverse proxy + Gateway controller + noVNC frontend. No business logic.

For the Gateway controller specifics, see `infra-gateway-controller.instructions.md`.

## Caddy Snippet Structure

The Caddyfile uses `import` directives to compose routing from snippet files:

```
infra/caddy/
  Caddyfile              # Shell: imports from sites/, domains/, and shared dirs
  docker-entrypoint.sh   # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH, then starts Caddy
  sites/
    ibkr-bridge.caddy    # SITE_DOMAIN route handlers (/ibkr/order, /ibkr/trades, /ibkr/ws/events, /health)
  domains/
    ibkr-vnc.caddy       # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
```

- **`sites/*.caddy`** contain `handle` blocks imported inside the `{$SITE_DOMAIN}` site definition. Routes must be prefixed with the project name (`/ibkr/*`) to avoid collisions across shared projects.
- **`domains/*.caddy`** contain full site blocks for additional domains (e.g. `{$VNC_DOMAIN}`).
- This structure allows multiple projects to share a single Caddy instance on the same droplet.

## Shared mode deploys

Shared projects deploy snippets to `/opt/caddy-shared/` on the droplet (not into the host project's directory). The host Caddy mounts:

- `./infra/caddy/sites/` → `/etc/caddy/sites/` (host project's own routes)
- `./infra/caddy/domains/` → `/etc/caddy/domains/` (host project's domain blocks)
- `/opt/caddy-shared/sites/` → `/etc/caddy/shared-sites/` (shared projects' routes)
- `/opt/caddy-shared/domains/` → `/etc/caddy/shared-domains/` (shared projects' domains)

During shared deploy, snippet files are **templated** — all `{$VAR}` placeholders are replaced with literal env var values from the shared project's `.env` before Caddy reads it. This avoids requiring the host Caddy container to have the shared project's env vars.

## Critical invariant: keep `route_prefixes` in sync

**When adding a new route under a new prefix (`infra/caddy/sites/*.caddy`), always update `route_prefixes` in `cli/__init__.py` in the same commit.** The CLI's `_validate_site_snippet_routes` checks every `handle` directive against `route_prefixes` during shared deploy — if a new snippet's prefix isn't listed, shared deployments abort.

Current `route_prefixes` (no trailing slashes — validation uses `startswith(f"{prefix}/")`):

```python
route_prefixes=["/ibkr"]
```

A single `/ibkr` prefix covers `/ibkr/order`, `/ibkr/trades`, `/ibkr/ws/events`, and any future `/ibkr/*` endpoints. Only add a new prefix entry if you introduce a fundamentally new top-level segment (e.g. `/v2`).

## VNC basic auth

- The entire VNC domain is protected by HTTP Basic Auth. Caddy's `basic_auth` directive uses a bcrypt hash of `VNC_SERVER_PASSWORD`, generated at container startup by `infra/caddy/docker-entrypoint.sh`.
- Username defaults to `admin`; override via `VNC_BASIC_AUTH_USER` env var.

## noVNC

- `infra/novnc/index.html` is a custom landing page served by the `theasp/novnc` image.
- The noVNC container has a healthcheck (RFB banner probe to `ib-gateway:5900` — reads the `RFB 003.xxx` string, not just TCP-open).
- Browser auto-retries on disconnect; `autoheal` restarts the container when the VNC backend is unreachable.
