# IBKR Bridge

REST API bridge to Interactive Brokers — place orders and list trades via HTTP, backed by the [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php). Deployed to a DigitalOcean droplet with a single `make deploy`.

> [!WARNING]
> This project is under active development and not yet ready for prime time. You're welcome to use it, but expect frequent breaking changes.

## Why This Project?

Interacting with IBKR programmatically normally requires maintaining a persistent TWS or Gateway connection and speaking the IB API protocol. This project wraps that complexity behind a simple REST API:

- **Place orders** via `POST /ibkr/order` with a JSON body
- **List trades** via `GET /ibkr/trades` — session trades + completed orders, deduplicated
- **Health check** via `GET /ibkr/health` — see connection status and trading mode
- **Automatic reconnection** — exponential backoff + watchdog, survives Gateway restarts
- **Browser-based 2FA** — noVNC container provides browser access to the Gateway GUI for authentication
- **Gateway lifecycle control** — restart the Gateway container from a web endpoint without SSH

All deployed behind **Caddy** with automatic HTTPS, on a single DigitalOcean droplet.

> **Looking for trade fill polling and webhook delivery?** See [ibkr_relay](https://github.com/tradegist/ibkr_relay) — a companion project that polls the Flex Web Service for fills and forwards them to your webhook URL.

## Table of Contents

- [API Endpoints](#api-endpoints)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
  - [Shared deploy](#shared-deploy)
- [Configuration](#configuration)
- [Domains & HTTPS](#domains--https)
- [Droplet Sizing](#droplet-sizing)
- [Commands](#commands)
- [Pause & Resume](#pause--resume)
- [Testing](#testing)
- [TypeScript Types](#typescript-types)
- [Project Structure](#project-structure)
- [Security](#security)
- [Current Status](#current-status)

## API Endpoints

All `/ibkr/*` endpoints require `Authorization: Bearer <API_TOKEN>` header.

#### Place an order

```
POST /ibkr/order
```

```json
{
  "contract": {
    "symbol": "AAPL",
    "secType": "STK",
    "exchange": "SMART",
    "currency": "USD"
  },
  "order": {
    "action": "BUY",
    "totalQuantity": 10,
    "orderType": "LMT",
    "lmtPrice": 150.0,
    "tif": "DAY"
  }
}
```

Response:

```json
{
  "status": "PreSubmitted",
  "orderId": 684196618,
  "action": "BUY",
  "symbol": "AAPL",
  "totalQuantity": 10,
  "orderType": "LMT",
  "lmtPrice": 150.0
}
```

#### List trades

```
GET /ibkr/trades
```

Returns session trades + completed orders, deduplicated by permanent order ID:

```json
{
  "trades": [
    {
      "orderId": 684196618,
      "action": "BUY",
      "totalQuantity": 10,
      "orderType": "LMT",
      "lmtPrice": 150.0,
      "tif": "DAY",
      "symbol": "AAPL",
      "secType": "STK",
      "exchange": "SMART",
      "currency": "USD",
      "status": "Filled",
      "filled": 10,
      "remaining": 0,
      "avgFillPrice": 149.95,
      "fills": [
        {
          "execId": "0001f4e8.67890abc.01.01",
          "time": "2026-04-10T10:30:00",
          "exchange": "ISLAND",
          "side": "BOT",
          "shares": 10,
          "price": 149.95,
          "commission": 1.0,
          "commissionCurrency": "USD",
          "realizedPNL": 0.0
        }
      ]
    }
  ]
}
```

#### Health check

```
GET /ibkr/health
```

Returns `{"connected": true, "tradingMode": "paper"}`. No auth required.

> **Note:** Caddy rewrites `/ibkr/health` to `/health` upstream. When accessing the bridge directly (e.g. `http://localhost:15101`), use `GET /health`.

#### WebSocket event stream

```
GET /ibkr/ws/events
```

Streams real-time trade execution events over WebSocket. Requires `Authorization: Bearer <API_TOKEN>` (sent as HTTP header during the upgrade handshake).

Query parameters:

- `last_seq` — replay buffered events with `seq >` this value (default: `0`)

Events are JSON envelopes wrapping bridge metadata (`type`, `seq`, `timestamp`, and on fill events `source`) around an inner `fill` payload whose nested `contract` / `execution` / `commissionReport` fields mirror `ib_async` 2.1.0 exactly:

```json
{
  "type": "commissionReportEvent",
  "seq": 42,
  "timestamp": "2026-04-11T10:30:00+00:00",
  "source": "live",
  "fill": {
    "contract": {
      "secType": "STK",
      "conId": 265598,
      "symbol": "AAPL",
      "exchange": "SMART",
      "primaryExchange": "NASDAQ",
      "currency": "USD",
      "..."
    },
    "execution": {
      "execId": "0001f4e8.67890abc.01.01",
      "time": "2026-04-11T10:30:00+00:00",
      "acctNumber": "UXXXXXXX",
      "exchange": "ISLAND",
      "side": "BOT",
      "shares": 10.0,
      "price": 149.95,
      "permId": 684196618,
      "cumQty": 10.0,
      "avgPrice": 149.95,
      "..."
    },
    "commissionReport": {
      "execId": "0001f4e8.67890abc.01.01",
      "commission": 1.0,
      "currency": "USD",
      "realizedPNL": 0.0,
      "yield_": 0.0,
      "yieldRedemptionDate": 0
    },
    "time": "2026-04-11T10:30:00+00:00"
  }
}
```

Event types:

- `execDetailsEvent` — fill executed (preliminary, may lack commission). Only emitted for live, same-user fills.
- `commissionReportEvent` — fill with commission data (confirmed). Emitted for live same-user fills **and** for fills surfaced by the cross-user reconcile path (see below).
- `connected` — bridge connected to IB Gateway
- `disconnected` — bridge lost IB Gateway connection

Each fill event carries a `source` field indicating provenance:

- `"live"` — emitted by ib_async's push callbacks (real-time, same-user fills)
- `"reconciled"` — emitted by the position-triggered `reqExecutions` path (typically cross-user fills, e.g. orders placed from the mobile app)

Status events (`connected` / `disconnected`) carry only `type`, `seq`, and `timestamp` — no `fill` or `source` field. The `WsEnvelope` TypeScript type is a discriminated union over `type`, so consumers narrow with a single `if (env.type === "commissionReportEvent") { ... }` and TypeScript guarantees `env.fill` and `env.source` are present.

Features:

- **Cross-user fill detection** — when a position changes (including from orders placed by another IBKR user on the same account, e.g. from the mobile app), the bridge calls `reqExecutions` and broadcasts each new fill as `commissionReportEvent` with `source: "reconciled"`. This complements the live callbacks, which IB only fires for fills the bridge's own user placed.
- **Server-side execId dedupe** — the bridge tracks every broadcast `execId` (keyed to its fill timestamp) and emits each fill at most once. Both the live and the reconcile broadcast paths check the dedupe map _before_ emitting, so whichever path wins the race wins — the other drops silently. The map persists across reconnects (a transient connection blip never re-broadcasts today's fills) and is pruned at the start of every reconcile to drop entries older than 2 days — bounding memory to roughly `fills_per_day × 2` entries.
- **Replay on reconnect** — pass `?last_seq=N` to receive buffered events since that sequence number
- **Ring buffer** — last 500 events buffered server-side (configurable via `WS_BUFFER_SIZE`)
- **Up to 10 simultaneous subscribers** (configurable via `WS_MAX_SUBSCRIBERS`)
- **Zombie detection** — server sends WebSocket pings every 30s (configurable via `WS_HEARTBEAT_INTERVAL`)

#### Gateway control (VNC domain)

```
POST /gateway/cgi-bin/start-gateway    # Start the ib-gateway container
GET  /gateway/cgi-bin/gateway-status   # Check ib-gateway container state
```

These endpoints are served on the **VNC domain** (not `SITE_DOMAIN`), accessible via the gateway-controller container. The entire VNC domain is protected by HTTP Basic Auth (username defaults to `admin`, password is `VNC_SERVER_PASSWORD`). The bcrypt hash for Caddy is auto-computed from `VNC_SERVER_PASSWORD` at deploy time using `htpasswd` — no manual hash generation needed on macOS/Linux. On Windows, set `VNC_BASIC_AUTH_HASH` in `.env` directly (see env var table below).

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  DigitalOcean Droplet                                    │
│                                                          │
│  ┌──────────────────────────────────────────────┐        │
│  │  caddy (reverse proxy + auto HTTPS)          │        │
│  │  trade.example.com → bridge:5000             │        │
│  │  vnc.example.com   → novnc:8080              │        │
│  │  vnc.example.com/gateway/* → controller:9000 │        │
│  │  Ports: 80 (HTTP→redirect), 443 (HTTPS)      │        │
│  └───────┬─────────────┬─────────────┬──────────┘        │
│          │             │             │                   │
│  ┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────────────┐    │
│  │  bridge      │ │  novnc   │ │ gateway-controller │    │
│  │  REST API    │ │  Browser │ │ Start/status via   │    │
│  │  Orders +    │ │  VNC GUI │ │ Docker socket      │    │
│  │  Trades      │ │          │ │                    │    │
│  └───────┬──────┘ └─────┬────┘ └────────────────────┘    │
│          │              │                                │
│  ┌───────▼──────────────▼──────┐                         │
│  │  ib-gateway                 │                         │
│  │  IBKR Gateway (Java)        │                         │
│  │  TWS API on ports 4003/4004 │                         │
│  │  VNC on port 5900           │                         │
│  └─────────────────────────────┘                         │
│                                                          │
│  Firewall: SSH from deployer IP only                     │
│  HTTP/HTTPS open (Caddy auto-redirects HTTP → HTTPS)     │
└──────────────────────────────────────────────────────────┘
```

Six containers in a single Docker network:

- **`ib-gateway`** — [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php) running in Docker via [gnzsnz/ib-gateway](https://github.com/gnzsnz/ib-gateway). Exposes the TWS API (port 4003 live / 4004 paper) and VNC (port 5900) for 2FA authentication. Configured with `AUTO_RESTART_TIME` (default `11:30 PM` in `TIME_ZONE`, `America/New_York` by default, overridable) so IBC proactively restarts before IBKR's nightly ~11:45 PM ET session reset, writing an autorestart file that allows the next start to skip 2FA. `restart: unless-stopped` keeps the container running across these daily restarts — typically requiring 2FA only once per week when IBKR expires the session.
- **`bridge`** — Python aiohttp service that connects to the Gateway via [ib_async](https://github.com/ib-api-reloaded/ib_async) and exposes the REST API. Handles auto-reconnection with exponential backoff and a periodic watchdog.
- **`novnc`** — [noVNC](https://novnc.com/) browser-based VNC client for accessing the Gateway GUI. Used for 2FA authentication and monitoring. Auto-reconnects in the browser on unclean disconnect.
- **`caddy`** — [Caddy 2](https://caddyserver.com/) reverse proxy with automatic HTTPS via Let's Encrypt. Routes API traffic to the bridge, VNC traffic to noVNC.
- **`gateway-controller`** — Lightweight CGI container with Docker socket access. Provides HTTP endpoints to start/check the Gateway container without SSH. Also runs a background monitor that watches for ib-gateway exits: on a 2FA timeout it stops the restarted container (preventing an infinite restart loop) and sends an alert email prompting you to authenticate via VNC; on any other unexpected exit it sends a crash alert.
- **`autoheal`** — Watches containers labelled `autoheal=true` and restarts them when they become unhealthy. Used to restart `novnc` when the VNC backend is unreachable.

## Quick Start

### Prerequisites

- [Docker Compose v2](https://docs.docker.com/compose/install/) (the Go rewrite, `docker compose`)
- [Terraform](https://developer.hashicorp.com/terraform/install) installed
- A [DigitalOcean API token](https://cloud.digitalocean.com/account/api/tokens)
- An IBKR account (paper or live)

### Steps

```bash
# 1. Clone and configure
git clone https://github.com/tradegist/ibkr_bridge.git
cd ibkr_bridge
make setup        # Create .venv, install dependencies, and copy env templates
# Edit .env with your IB credentials, domains, and API token
# Edit .env.droplet with your deployment config (DEPLOY_MODE, DO_API_TOKEN, etc.)

# 2. Deploy
make deploy

# 3. Complete 2FA
# Open https://vnc.example.com in your browser and authenticate

# 4. Verify connection
curl -s https://trade.example.com/ibkr/health | python3 -m json.tool
# Should show: {"connected": true, "tradingMode": "paper"}

# 5. Place a test order (paper mode)
make order Q=1 SYM=AAPL T=MKT

# 6. Tear down when done
make destroy
```

### Shared deploy

Use shared mode (`DEPLOY_MODE=shared`) when ibkr_bridge runs on a droplet that is already hosting another project (e.g. relayport). Caddy is provided by the host — ibkr_bridge deploys Caddy snippets to `/opt/caddy-shared/` and connects to the host's shared Docker network.

```bash
# Set in .env.droplet (CLI-only config)
DEPLOY_MODE=shared
DROPLET_IP=1.2.3.4        # provided by the host project owner
SSH_KEY=~/.ssh/shared-key # provided by the host project owner

# Set in .env (pushed to the droplet so Compose can expand it)
SHARED_NETWORK=relay-net  # must match the host project's network name

make deploy
```

#### VNC basic auth hash

`make deploy` templates the Caddy snippet with a bcrypt hash derived from `VNC_SERVER_PASSWORD`. How it is computed depends on your OS:

**macOS** — `htpasswd` is built-in. `make deploy` computes the hash automatically, nothing to do.

**Linux** — install `htpasswd` first, then `make deploy` handles the rest:

```bash
apt install apache2-utils   # Debian/Ubuntu
```

**Windows** — `htpasswd` is not available. Pre-compute the hash in Git Bash or WSL and add it to `.env` before running `make deploy`:

```bash
htpasswd -nbB admin yourpassword
# Output: admin:$2y$05$...
# Copy only the hash part (after "admin:") into .env:
VNC_BASIC_AUTH_HASH=$2y$05$...
```

### Local development

Run the stack locally without TLS or Caddy:

```bash
make local-up     # build and start all services
make local-down   # stop and remove
```

Endpoints after startup:

| Service  | URL                           |
| -------- | ----------------------------- |
| REST API | http://localhost:15101/health |
| VNC      | http://localhost:15100        |

#### Updating the local stack after code changes

`docker-compose.local.yml` adds read-only bind mounts that shadow the baked-in image files with your local source tree. Code changes are visible on container restart — no rebuild needed:

```bash
make sync ENV=local          # restart all containers
```

`make local-up` is only needed for the initial build or after changing `requirements.txt` / Dockerfile.

## Configuration

Configuration is split into two files to separate container config from CLI-only deployment config.

`make setup` copies the templates from `env_examples/` automatically on first run.

### `.env` — app config (pushed to server, injected into containers)

| Variable                | Required    | Default                 | Description                                                                                                                                                                                                                                                                                   |
| ----------------------- | ----------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TWS_USERID`            | Yes         | —                       | IBKR username                                                                                                                                                                                                                                                                                 |
| `TWS_PASSWORD`          | Yes         | —                       | IBKR password                                                                                                                                                                                                                                                                                 |
| `VNC_SERVER_PASSWORD`   | Yes         | —                       | Password for VNC access to the Gateway GUI                                                                                                                                                                                                                                                    |
| `TRADING_MODE`          | No          | `paper`                 | `paper` or `live`                                                                                                                                                                                                                                                                             |
| `VNC_DOMAIN`            | Yes         | —                       | Domain for VNC access (e.g. `vnc.example.com`)                                                                                                                                                                                                                                                |
| `SITE_DOMAIN`           | Yes         | —                       | Domain for the REST API (e.g. `trade.example.com`)                                                                                                                                                                                                                                            |
| `API_TOKEN`             | Yes         | —                       | Bearer token for `/ibkr/*` endpoints (`openssl rand -hex 32`)                                                                                                                                                                                                                                 |
| `JAVA_HEAP_SIZE`        | No          | `768`                   | IB Gateway Java heap in MB. Determines auto-selected droplet size.                                                                                                                                                                                                                            |
| `TIME_ZONE`             | No          | `America/New_York`      | Timezone (tz database format)                                                                                                                                                                                                                                                                 |
| `AUTO_RESTART_TIME`     | No          | `11:30 PM`              | IBC scheduled daily restart time (`HH:MM AM/PM`, interpreted in `TIME_ZONE`). The default `11:30 PM` is 15 minutes before IBKR's nightly ~11:45 PM ET session reset — if `TIME_ZONE` is not `America/New_York`, set this accordingly. Writes an autorestart file so the next start skips 2FA. |
| `VNC_BASIC_AUTH_USER`   | No          | `admin`                 | Username for VNC domain basic auth                                                                                                                                                                                                                                                            |
| `VNC_BASIC_AUTH_HASH`   | No          | auto-computed           | Bcrypt hash for VNC basic auth. Auto-derived from `VNC_SERVER_PASSWORD` at deploy time via `htpasswd` (macOS built-in; `apt install apache2-utils` on Linux). On Windows, pre-compute with `htpasswd -nbB admin <password>` and set here.                                                     |
| `WS_BUFFER_SIZE`        | No          | `500`                   | Ring buffer size for WebSocket event replay on client reconnect                                                                                                                                                                                                                               |
| `WS_MAX_SUBSCRIBERS`    | No          | `10`                    | Maximum simultaneous WebSocket subscribers                                                                                                                                                                                                                                                    |
| `WS_HEARTBEAT_INTERVAL` | No          | `30`                    | WebSocket ping interval in seconds for zombie connection detection                                                                                                                                                                                                                            |
| `SHARED_NETWORK`        | Shared mode | —                       | Docker network name for cross-project communication (e.g. `relay-net`). Must be set in `.env` (not `.env.droplet`) so it gets pushed to the droplet for Compose to expand. Required when `DEPLOY_MODE=shared`. The CLI idempotently creates this network on the droplet and applies `docker-compose.shared-network.yml` to mark it `external: true`. |
| `RESEND_API_KEY`        | No          | —                       | [Resend](https://resend.com) API key. Required to enable crash alerting.                                                                                                                                                                                                                      |
| `ALERT_REPORT_EMAIL_TO` | No          | —                       | Recipient address for crash alert emails. Required to enable crash alerting.                                                                                                                                                                                                                  |
| `ALERT_EMAIL_FROM`      | No          | `onboarding@resend.dev` | Sender address for alert emails. Must be a domain verified in your Resend account. Defaults to Resend's shared address if unset.                                                                                                                                                              |

### `.env.droplet` — developer machine only (never pushed to server)

The name reflects its origin (droplet infrastructure config) but its scope is broader: any var that belongs on the developer's machine rather than the server lives here — deployment credentials, SSH keys, and local CLI preferences like `DEFAULT_CLI_ENV`.

| Variable          | Required | Default              | Description                                                                                           |
| ----------------- | -------- | -------------------- | ----------------------------------------------------------------------------------------------------- |
| `DEPLOY_MODE`     | Yes      | —                    | `standalone` (own droplet via Terraform) or `shared` (deploy to existing droplet)                     |
| `DO_API_TOKEN`    | Yes\*    | —                    | DigitalOcean API token (standalone mode only — can be removed after first deploy)                     |
| `DROPLET_IP`      | Yes\*    | —                    | Droplet IP (from Terraform output in standalone; provided by host in shared)                          |
| `SSH_KEY`         | No       | `~/.ssh/ibkr-bridge` | SSH key path — **shared mode only**. In standalone, Terraform auto-generates the key; never set this. |
| `DROPLET_SIZE`    | No       | (auto)               | Override droplet size slug (e.g. `s-1vcpu-2gb`). When set, ignores `JAVA_HEAP_SIZE` for sizing.       |
| `DEFAULT_CLI_ENV` | No       | `prod`               | Default CLI target: `prod` (HTTPS via `SITE_DOMAIN`) or `local` (localhost:15101)                     |

\* `DO_API_TOKEN` is required for standalone mode only (first deploy). `DROPLET_IP` is set automatically by Terraform output in standalone, or provided by the host in shared mode.

## Domains & HTTPS

Two domain names are **required**. Caddy uses them to automatically provision TLS certificates from Let's Encrypt.

### Setup

1. Point both domains to the droplet's reserved IP as **A records**:
   ```
   trade.example.com  A  1.2.3.4
   vnc.example.com    A  1.2.3.4
   ```
2. Set them in `.env`:
   ```
   SITE_DOMAIN=trade.example.com
   VNC_DOMAIN=vnc.example.com
   ```
3. Start the stack — Caddy will automatically obtain and renew the certificates.

## Droplet Sizing

The droplet size is auto-selected based on `JAVA_HEAP_SIZE`:

| Heap (MB) | Droplet        | RAM   |
| --------- | -------------- | ----- |
| ≤ 1024    | `s-1vcpu-2gb`  | 2 GB  |
| ≤ 3072    | `s-2vcpu-4gb`  | 4 GB  |
| ≤ 6144    | `s-4vcpu-8gb`  | 8 GB  |
| > 6144    | `s-8vcpu-16gb` | 16 GB |

Override with `DROPLET_SIZE` in `.env` to use a specific slug regardless of heap size.

## Commands

All operations are available via `make` or the Python CLI directly. Run `make help` to see the full list:

```
  make deploy      Deploy infrastructure (Terraform + Docker)
  make destroy     Permanently destroy all infrastructure
  make pause       Snapshot droplet + delete (save costs)
  make resume      Restore droplet from snapshot
  make setup       Create .venv and install all dependencies
  make sync        Push .env + restart (S=service B=1 LOCAL_FILES=1 SKIP_POST_CHECK=1 ENV=local)
  make sanity-check-deployment  Run claude sanity check against the droplet
  make order       Place a stock order (Q=qty SYM=symbol T=type [P=price] ...)
  make types       Regenerate TypeScript types from Pydantic models
  make test        Run unit tests (pytest)
  make typecheck   Run mypy strict type checking
  make lint        Run ruff linter (FIX=1 to auto-fix)
  make e2e         Run E2E tests (starts/stops stack automatically)
  make e2e-up      Start E2E test stack (ib-gateway + bridge)
  make e2e-run     Run E2E tests (stack must be up)
  make e2e-down    Stop and remove E2E test stack
  make local-up    Start full stack locally (no TLS, direct port access)
  make local-down  Stop local stack
  make logs        Stream logs (S=service ENV=local)
  make stats       Show container resource usage
  make gateway     Start IB Gateway + show connection status
  make ssh         SSH into the droplet
  make help        Show available commands
```

You can also invoke the CLI directly with `python3 -m cli <command>`:

```bash
python3 -m cli deploy
python3 -m cli sync bridge
python3 -m cli order 10 AAPL MKT              # BUY 10 AAPL at market
python3 -m cli order -5 TSLA LMT 250.00       # SELL 5 TSLA at $250 limit
python3 -m cli pause
python3 -m cli resume
python3 -m cli destroy
```

`make` examples:

```bash
make deploy                            # provision droplet + start containers
make sync                              # push .env + restart all services
make sync S=bridge                     # push .env + restart bridge only
make sync B=1                          # push .env + rebuild images + restart
make sync LOCAL_FILES=1                # rsync files + rebuild + restart (full deploy)
make order Q=10 SYM=AAPL T=MKT        # BUY 10 AAPL at market
make order Q=-5 SYM=TSLA T=LMT P=250  # SELL 5 TSLA limit $250
make order Q=1 SYM=AAPL T=LMT P=150 TIF=GTC         # GTC order
make order Q=1 SYM=AAPL T=LMT P=150 RTH=1            # Allow outside RTH
make order Q=100 SYM=SAP T=MKT CUR=EUR EX=IBIS       # European exchange
make test                              # run unit tests
make typecheck                         # strict mypy checking
make lint                              # run ruff linter
make logs                              # stream service logs (droplet)
make logs S=bridge ENV=local           # stream local bridge logs
```

### Which service to sync

After changing a variable in `.env`, restart only the affected service:

| Variable                | Service            | Command                          |
| ----------------------- | ------------------ | -------------------------------- |
| `API_TOKEN`             | bridge             | `make sync S=bridge`             |
| `TRADING_MODE`          | bridge, ib-gateway | `make sync`                      |
| `TWS_USERID/PASSWORD`   | ib-gateway         | `make sync S=gateway`            |
| `VNC_SERVER_PASSWORD`   | ib-gateway         | `make sync S=gateway`            |
| `SITE_DOMAIN`           | caddy              | `make sync S=caddy`              |
| `VNC_DOMAIN`            | caddy              | `make sync S=caddy`              |
| `RESEND_API_KEY`        | gateway-controller | `make sync S=gateway-controller` |
| `ALERT_REPORT_EMAIL_TO` | gateway-controller | `make sync S=gateway-controller` |
| `ALERT_EMAIL_FROM`      | gateway-controller | `make sync S=gateway-controller` |
| Multiple or unsure      | all                | `make sync`                      |

### Syncing code changes

#### Local stack

When `DEFAULT_CLI_ENV=local` (or `ENV=local`), `make sync` restarts all containers. Bind mounts in `docker-compose.local.yml` ensure code changes are picked up automatically:

```bash
make sync              # restart (when DEFAULT_CLI_ENV=local)
make sync ENV=local    # explicit override
```

#### Remote droplet

`make sync` only pushes `.env` and restarts containers. When you change Python code, Dockerfiles, or Compose config, use `LOCAL_FILES=1`:

```bash
make sync LOCAL_FILES=1
```

This runs a pre-deploy pipeline (branch check → clean tree → typecheck → tests → rsync → rebuild).

## Post-Deploy Sanity Check

After `make sync LOCAL_FILES=1` and `make deploy`, the CLI runs a best-effort sanity check that SSHes into the droplet, captures `docker compose ps` and the last 100 lines of logs from the past 5 minutes, then asks the local `claude` CLI to summarize the state as a one-line `[GREEN|YELLOW|RED]` verdict. Claude runs as a pure text summarizer (no tool access) — SSH/docker invocation happens from Python, so there's no agent-driven shell execution.

The check is best-effort: missing `claude` binary, SSH failure, network/auth/rate-limit errors, or a 60s timeout each produce a one-line warning and never abort the deploy. Opt out via `SKIP_POST_DEPLOY_CHECK=1`, `--skip-post-check`, or `make sync SKIP_POST_CHECK=1` / `make deploy SKIP_POST_CHECK=1`.

Run on demand without syncing:

```bash
make sanity-check-deployment
```

## Pause & Resume

To stop billing for the droplet without losing state:

```bash
make pause       # snapshot droplet + delete
make resume      # restore from snapshot + reassign IP
```

**Costs while paused:**

- Droplet: **$0** (deleted)
- Snapshot: ~$0.06/GB/month
- Reserved IP: **$5/month** while unassigned (free when assigned)

After resume, open the VNC URL to complete 2FA — the Gateway needs re-authentication.

## Testing

```bash
make test        # run unit tests (pytest)
make typecheck   # strict mypy checking
make lint        # run ruff linter (FIX=1 to auto-fix)
```

### E2E tests

E2E tests run against a local Docker stack (`docker-compose.test.yml`) with the IB Gateway and bridge service. **Paper account credentials are required** — real orders are placed in paper mode.

```bash
make e2e          # start stack → run tests → stop stack
make e2e-up       # start test stack (waits up to 240s for Gateway connection)
make e2e-run      # run E2E tests (stack must be up)
make e2e-down     # stop and remove test stack
```

- Credentials live in `.env.test` (gitignored). Template: `env_examples/env.test`.
- `make e2e-run` restarts the `bridge` container to pick up code changes from volume mounts.
- Test bridge runs on `localhost:15010` with token `test-token`.
- Gateway startup takes 30–120 seconds (Java + IBKR authentication). The `e2e-up` target waits up to 240 seconds and detects session conflicts (another TWS/Gateway using the same credentials).

## TypeScript Types

API types are available as a TypeScript package under `types/typescript/`:

```
types/typescript/
  index.d.ts                 # Top-level barrel (hand-maintained; namespace map)
  package.json               # @tradegist/ibkr-bridge-types
  http/
    index.d.ts               # Auto-generated barrel (re-exports every public type)
    types.d.ts               # Auto-generated from bridge_models.py SCHEMA_MODELS
    types.schema.json        # Intermediate JSON Schema
```

Everything under `http/` is regenerated by `make types`. The discriminated union `WsEnvelope` is emitted automatically by the JSON Schema → TypeScript pipeline — no hand-edited union aliases.

Usage:

```typescript
import type { IbkrBridgeHttp } from "@tradegist/ibkr-bridge-types";

const req: IbkrBridgeHttp.PlaceOrderPayload = {
  contract: { symbol: "AAPL", secType: "STK", exchange: "SMART", currency: "USD" },
  order: { action: "BUY", totalQuantity: 10, orderType: "MKT" },
};

const resp: IbkrBridgeHttp.PlaceOrderResponse = ...;
const trades: IbkrBridgeHttp.ListTradesResponse = ...;
```

Types are auto-generated from the Pydantic models via `make types`. The package is not yet published to npm.

## Project Structure

```
├── Makefile                       # CLI shortcuts (make deploy, make order, etc.)
├── cli/                           # Python CLI (replaces shell scripts)
│   ├── __init__.py                # Project-specific config (CoreConfig, helpers)
│   ├── __main__.py                # Entry point (python3 -m cli <command>)
│   ├── order.py                   # Place an order via CLI
│   └── core/                      # Project-agnostic (reusable across projects)
│       ├── __init__.py            # CoreConfig dataclass, generic helpers
│       ├── deploy.py              # Standalone (Terraform) or shared (rsync + compose)
│       ├── destroy.py             # Terraform destroy
│       ├── pause.py               # Snapshot + delete droplet
│       ├── resume.py              # Restore from snapshot
│       └── sync.py                # rsync files + pre-deploy checks + restart
├── env_examples/                  # Configuration templates (make setup copies to .<name>)
│   ├── env                        # App config template (.env)
│   └── env.droplet                # CLI-only deployment config template (.env.droplet)
├── docker-compose.yml                # Container orchestration (6 services)
├── docker-compose.shared.yml         # Shared-mode overlay (disables Caddy)
├── docker-compose.shared-network.yml # Marks SHARED_NETWORK as external (CLI pre-creates)
├── docker-compose.local.yml          # Local dev override (direct port access, no TLS)
├── docker-compose.test.yml           # E2E test override (paper mode, no Caddy/noVNC)
├── services/
│   └── bridge/
│       ├── Dockerfile             # Python 3.11-slim
│       ├── requirements.txt       # ib_async, aiohttp, httpx, pydantic
│       ├── main.py                # Entrypoint (IB connection + HTTP server)
│       ├── bridge_models.py       # Pydantic models (request/response types)
│       ├── client/                # IB Gateway client
│       │   ├── __init__.py        # IBClient (connection, reconnection, watchdog, fill reconcile)
│       │   ├── event_hub.py       # EventHub (pub/sub broadcast + ring buffer for WS replay)
│       │   ├── orders.py          # OrdersNamespace (place orders)
│       │   └── trades.py          # TradesNamespace (list trades + fills)
│       ├── bridge_routes/         # HTTP API
│       │   ├── __init__.py        # Route orchestrator (create_routes)
│       │   ├── constants.py       # Shared constants (AUTH_PREFIX, client_key, hub_key)
│       │   ├── health.py          # GET /health handler
│       │   ├── middlewares.py     # Auth middleware (Bearer token, HMAC-safe)
│       │   ├── order_place.py     # POST /ibkr/order handler
│       │   ├── trades_list.py     # GET /ibkr/trades handler
│       │   └── ws_events.py       # GET /ibkr/ws/events WebSocket handler
│       └── tests/e2e/             # E2E tests
│           ├── conftest.py        # httpx fixtures (api + anon_api)
│           └── test_smoke.py      # Health + auth smoke tests
├── infra/
│   ├── caddy/
│   │   ├── Caddyfile              # Reverse proxy config (SITE_DOMAIN + VNC_DOMAIN)
│   │   ├── docker-entrypoint.sh   # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH
│   │   ├── sites/
│   │   │   └── ibkr-bridge.caddy  # SITE_DOMAIN API routes (/ibkr/order, /ibkr/trades, /ibkr/ws/events, /health)
│   │   └── domains/
│   │       └── ibkr-vnc.caddy    # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
│   ├── gateway-controller/
│   │   ├── Dockerfile             # Alpine + docker CLI for container control
│   │   ├── entrypoint.sh          # Starts monitor-gateway in background, then execs httpd
│   │   ├── start-gateway.sh       # CGI: start ib-gateway container
│   │   ├── gateway-status.sh      # CGI: check ib-gateway status
│   │   └── monitor-gateway.sh     # Background daemon: watch for unexpected exits, send Resend alert
│   └── novnc/
│       └── index.html             # Custom noVNC landing page
├── terraform/
│   ├── main.tf                    # Droplet, firewall, reserved IP, SSH key
│   ├── variables.tf               # Terraform variables
│   ├── outputs.tf                 # Droplet IP, VNC URL, Site URL, SSH key
│   └── cloud-init.sh             # Docker install + project directory
├── schema_gen.py                  # JSON Schema generator (Pydantic → TS types)
└── types/
    ├── typescript/                # @tradegist/ibkr-bridge-types npm package
    │   ├── index.d.ts             # Barrel: exports IbkrBridgeHttp namespace
    │   ├── package.json
    │   └── http/                  # IbkrBridgeHttp namespace
    │       ├── index.d.ts
    │       └── types.d.ts         # Generated from bridge_models.py SCHEMA_MODELS
    └── python/                    # ibkr-bridge-types PyPI package
        └── ibkr_bridge_types/
```

## Security

- Firewall restricts SSH (22) to the deployer's IP only
- HTTP/HTTPS open (Caddy auto-redirects HTTP → HTTPS)
- All `/ibkr/*` endpoints require Bearer token (HMAC-safe comparison)
- Empty `API_TOKEN` returns HTTP 500 (prevents `hmac.compare_digest("", "")` bypass)
- VNC access is password-protected (`VNC_SERVER_PASSWORD`)
- No credentials stored in the repository

## Current Status

- [x] Terraform infrastructure (droplet, firewall, SSH key, reserved IP)
- [x] Docker Compose orchestration (5 containers)
- [x] IB Gateway connection with auto-reconnect + watchdog
- [x] Place orders (market + limit, with TIF and outside-RTH support)
- [x] List trades (session + completed, deduplicated)
- [x] Gateway lifecycle control (start/status via HTTP)
- [x] Browser-based VNC for 2FA
- [x] HTTPS via Caddy + Let's Encrypt
- [x] Makefile CLI (`make deploy`, `make order`, etc.)
- [x] TypeScript type definitions (`@tradegist/ibkr-bridge-types`, not yet published)
- [x] E2E tests against paper account
- [x] Deploy modes: standalone (Terraform) + shared (existing droplet)
- [x] Crash alerting via email (Resend) — 2FA timeouts stop the container + send alert, unexpected exits send a crash alert
- [x] VNC auto-reconnect — browser retries on disconnect, no manual refresh needed
- [ ] Cancel order endpoint
