# Runbook: VNC stuck on "Connecting" / wss "Pending"

> **For agents executing this runbook:** the placeholders `vnc.example.com`, `<DROPLET_IP>`, and the omitted SSH `-i` key path are intentional. Ask the user for the real domain, droplet IP, and SSH key path before running any command.

The commands below resolve the Caddy container by Compose label rather than hardcoding a name, so they work in both deploy modes (standalone → `ibkr-bridge-caddy-1`, shared → `relayport-caddy-1`) and survive replica-suffix changes.

**First seen:** 2026-04-27. **Status:** intermittent, root cause not pinned down.

## Symptom

- `https://vnc.example.com/` loads (the noVNC HTML appears) but the status stays at **"Connecting"** indefinitely.
- In browser DevTools → Network, the `wss://vnc.example.com/websockify` row shows:
  - Status: `(pending)` / Time: `Pending`
  - **Protocol column: empty**
  - Headers tab: "Provisional headers are shown" (no `Authorization`, no `:method`, etc.)

If you see status `401`, `404`, or `502` instead, this is a different problem — do not follow this runbook.

## Quick recovery (try first, ~30 seconds)

The `caddy reload` is graceful so it won't drop in-flight VNC sessions, but it does reset listeners and has empirically unstuck this state:

```sh
ssh root@<DROPLET_IP> '
  CADDY=$(docker ps --filter label=com.docker.compose.service=caddy --format "{{.Names}}" | head -1)
  docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile
'
```

Then the user reloads the browser tab (preferably in a fresh incognito window — see "Fresh-state test" below).

If reload doesn't help: restart caddy hard (`docker restart "$CADDY"`, or look up the name as above and `docker restart <name>`). This *will* drop existing VNC sessions.

## What to check before assuming this runbook applies

Confirm the backend is actually healthy — if it's not, fix that instead:

```sh
ssh root@<DROPLET_IP> \
  "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ib-gateway|novnc|caddy'"
```

All three should be `Up ... (healthy)` (or just `Up` for caddy, which has no healthcheck). Then verify the internal proxy chain end-to-end:

```sh
ssh root@<DROPLET_IP> "
  docker exec ibkr-bridge-novnc-1 python3 -c \"
import socket; s=socket.socket(); s.settimeout(3);
s.connect(('ib-gateway',5900)); print(repr(s.recv(16))); s.close()\"
"
```

Expect `b'RFB 003.008\n'`. If you get a timeout or a different banner, the issue is x11vnc, not Caddy — see the ib-gateway healthcheck in `docker-compose.yml` for the failure mode it covers.

## Diagnosis

Diagnostic access logging is enabled on the `vnc.example.com` site (added 2026-04-27). It writes to Caddy's stdout. Pull the recent `/websockify` entries:

```sh
ssh root@<DROPLET_IP> '
  CADDY=$(docker ps --filter label=com.docker.compose.service=caddy --format "{{.Names}}" | head -1)
  docker logs --since 1h "$CADDY" 2>&1 | grep "log0" | grep "/websockify"
'
```

What to look for in each entry:

| Field | Healthy | Failure mode |
| --- | --- | --- |
| `proto` | `HTTP/1.1` | h2 with no successful 101 below it |
| `tls.proto` | `http/1.1` | `h2` (means Chrome tried Extended CONNECT — see Caddy issue #7309) |
| `method` | `GET` | `CONNECT` (h2 Extended CONNECT) |
| `status` | `101` | Anything else, especially missing entirely |
| `duration` | seconds-to-hours (the WS session length) | < 1s with non-101 status, OR very long with non-101 |
| `user_id` | `admin` | empty (basic_auth credentials weren't sent) |

**Most informative signal:** if you can reproduce the failure but **no `/websockify` log entry appears**, the request never reached Caddy — likely a stuck TLS handshake on the WebSocket-only TCP socket the browser opens. This was the failure mode on 2026-04-27.

## Fresh-state test

To distinguish a real issue from a stuck browser/network connection:

1. Close all tabs to `vnc.example.com`.
2. Open a **new incognito window** (forces fresh TLS session, no cached state).
3. Visit `https://vnc.example.com/`, authenticate, watch the WS row in DevTools.

If incognito works but a normal window doesn't, the user's browser/network stack is the culprit, not the server.

## What we ruled out on 2026-04-27 (don't re-investigate without new evidence)

- **Backend chain unhealthy.** ib-gateway, novnc, x11vnc were all healthy. RFB banner probe in the healthcheck (added in commit `c17b85a`) was passing.
- **CLOSE_WAIT socket accumulation on x11vnc.** `/proc/net/tcp` showed only the LISTEN socket. Not the same failure mode as the earlier outage.
- **Basic auth not being forwarded by browser to WebSocket.** Successful logs show `user_id: admin` and `Authorization: REDACTED` — Chrome does forward cached basic_auth on same-origin WS upgrades.
- **HTTP/2 Extended CONNECT (RFC 8441) mishandling.** Initial hypothesis. Refuted by the actual successful access logs: Chrome opens a separate TLS connection for the WebSocket and ALPN-negotiates `http/1.1`, never using h2 Extended CONNECT. The `--http2` curl test that "proved" the h2 hypothesis was a false signal — synthetic h2 with `Upgrade: websocket` headers isn't what browsers actually do.
- **`$$l` escape in the docker-compose healthcheck.** Real bug, fixed earlier the same day, but unrelated to this symptom — the healthcheck was passing by the time we hit this issue.

## What's still uncertain

We don't have logs from a failing instance yet (diagnostic logging was only added *after* the failure cleared). Leading hypothesis: a stuck TLS handshake on the WebSocket-specific TCP socket Caddy serves on, possibly cleared by `caddy reload` resetting listeners. Confirming this requires either:

- Catching the issue with diagnostic logging on (next failure → check whether `/websockify` is logged at all)
- Packet capture during the failure (`tcpdump -i any port 443 -w /tmp/vnc.pcap` on the droplet)

Until we have either, treat the cause as "unknown, suspected stuck TLS state, recovers with caddy reload."

## Removing the diagnostic logging

When the issue is confirmed gone (e.g., 30+ days without recurrence, or a confirmed root-cause fix), remove the `log` block from:

- `infra/caddy/domains/ibkr-vnc.caddy` (local source, marked with a 2026-04-27 comment)
- `/opt/caddy-shared/domains/ibkr-vnc.caddy` (deployed copy on the droplet)

Then run the `caddy reload` snippet from the "Quick recovery" section above.
