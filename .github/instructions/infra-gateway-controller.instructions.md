---
applyTo: "infra/gateway-controller/**"
---

# `infra/gateway-controller/` — IB Gateway lifecycle + crash monitor

Alpine + Docker CLI container. Provides HTTP endpoints to start/check the `ib-gateway` container without SSH, and runs a background daemon that watches for unexpected exits.

The container mounts the host Docker socket (`/var/run/docker.sock`). Caddy rewrites `/gateway/*` → `/cgi-bin/*` before proxying. Exposed at `https://{VNC_DOMAIN}/gateway/*` and protected by the VNC-domain HTTP Basic Auth (see `infra.instructions.md`).

## Endpoints

- **`POST /cgi-bin/start-gateway`** — starts the `ib-gateway` container via Docker socket.
- **`GET /cgi-bin/gateway-status`** — returns the `ib-gateway` container state.

Served via busybox `httpd` as CGI scripts (`start-gateway.sh`, `gateway-status.sh`). The `Content-Type` and CORS headers in each script switch on `$ENV` (`local` allows `Access-Control-Allow-Origin: *`).

## Container discovery — Compose labels (MANDATORY)

Both CGI scripts AND `monitor-gateway.sh` use Compose labels to locate the `ib-gateway` container, **never hardcoded container names**:

```sh
label_filters="--filter label=com.docker.compose.service=ib-gateway"
if [ -n "$COMPOSE_PROJECT_NAME" ]; then
  label_filters="--filter label=com.docker.compose.project=$COMPOSE_PROJECT_NAME $label_filters"
fi
```

This is robust across:
- Project name changes (e.g. `ibkr-bridge` vs `ibkr-bridge-test`).
- Compose naming conventions (`<project>-<service>-1` vs `<project>_<service>_1`).
- Multi-instance hosts (the `COMPOSE_PROJECT_NAME` filter prevents cross-project matches).

When adding scripts that need to find other containers, follow the same label-based pattern.

## `monitor-gateway.sh` — background crash daemon

Watches `docker events` for `ib-gateway` `die` events and emails a Resend alert on each unexpected exit. Lifecycle started by `entrypoint.sh`: launches `monitor-gateway` in the background, then `exec`s `httpd`.

### Docker events format (Docker 29+)

- Uses `{{.Actor.ID}}` format. **Required for Docker 29+.** Older Docker versions used different field names. If you bump the `docker:XX-cli` image, verify the events output format is still compatible.

### 2FA exit handling

After the weekly IBKR session expiry, the container exits needing 2FA. Because `ib-gateway` has `restart: unless-stopped`, Docker will restart it — but the restarted instance also exits immediately because 2FA isn't satisfied. To break the loop:

1. On a `die` event, `monitor-gateway` tails the last 50 log lines.
2. If `"Second Factor Authentication"` is present, it's a 2FA timeout — `stop_gateway()` runs `docker stop` against any `running` or `restarting` containers matching the label filters (retries up to 5 times, 2s apart) to break the `restart: unless-stopped` loop.
3. Sends a "needs 2FA" alert email via Resend.
4. Otherwise (crash without 2FA prompt), just sends a crash-alert email — Docker's `restart: unless-stopped` continues to retry.

### Required env vars

- **`RESEND_API_KEY`** — required for alert emails.
- **`ALERT_REPORT_EMAIL_TO`** — required, recipient address.
- **`ALERT_EMAIL_FROM`** — optional, sender. Falls back to `onboarding@resend.dev`.

### Resilience

- The monitor restarts itself if the `docker events` pipe closes.
- Per-event errors do not abort the loop — the watcher keeps running.

## CGI script conventions

- POSIX `sh` only. No bashisms. The container ships busybox.
- POSIX `sh` has no arrays — `monitor-gateway` uses positional params (`set -- --filter "label=..."`) so each `--filter` value stays a single shell word even with whitespace.
- Errors should print structured JSON: `printf '{"error":"method not allowed"}'`.
- Always set `Content-Type: application/json` (and `Access-Control-Allow-Origin: *` when `$ENV=local`).

## Caddy routing

The VNC site (`infra/caddy/domains/ibkr-vnc.caddy`) routes:
- `/gateway/start` → `gateway-controller:80/cgi-bin/start-gateway`
- `/gateway/status` → `gateway-controller:80/cgi-bin/gateway-status`

All `/gateway/*` paths are protected by the VNC-domain basic auth. The bcrypt hash for `basic_auth` is generated at Caddy container startup by `infra/caddy/docker-entrypoint.sh` from `VNC_SERVER_PASSWORD`.
