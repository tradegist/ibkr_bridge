# IBKR Bridge

REST API bridge to Interactive Brokers — place orders and list trades via HTTP, backed by the [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php). Deployed to a DigitalOcean droplet with a single `make deploy`.

> [!WARNING]
> This project is under active development and not yet ready for prime time. You're welcome to use it, but expect frequent breaking changes.

## Why This Project?

Interacting with IBKR programmatically normally requires maintaining a persistent TWS or Gateway connection and speaking the IB API protocol. This project wraps that complexity behind a simple REST API:

- **Place orders** via `POST /ibkr/order` with a JSON body
- **List trades** via `GET /ibkr/trades` — session trades + completed orders, deduplicated
- **Health check** via `GET /health` — see connection status and trading mode
- **Automatic reconnection** — exponential backoff + watchdog, survives Gateway restarts
- **Browser-based 2FA** — noVNC container provides browser access to the Gateway GUI for authentication
- **Gateway lifecycle control** — restart the Gateway container from a web endpoint without SSH

All deployed behind **Caddy** with automatic HTTPS, on a single DigitalOcean droplet.

> **Looking for trade fill polling and webhook delivery?** See [ibkr_relay](https://github.com/tradegist/ibkr_relay) — a companion project that polls the Flex Web Service for fills and forwards them to your webhook URL.

## Table of Contents

- [API Endpoints](#api-endpoints)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
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
GET /health
```

Returns `{"connected": true, "tradingMode": "paper"}`. No auth required.

#### Gateway control (VNC domain)

```
POST /gateway/start-gateway    # Start the ib-gateway container
GET  /gateway/gateway-status   # Check ib-gateway container state
```

These endpoints are served on the **VNC domain** (not `SITE_DOMAIN`), accessible via the gateway-controller container. The entire VNC domain is protected by HTTP Basic Auth (username defaults to `admin`, password is `VNC_SERVER_PASSWORD`). Override the username with `VNC_BASIC_AUTH_USER` in `.env`.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  DigitalOcean Droplet                                    │
│                                                          │
│  ┌──────────────────────────────────────────────┐        │
│  │  caddy (reverse proxy + auto HTTPS)          │        │
│  │  trade.example.com → bridge:5000             │        │
│  │  vnc.example.com   → novnc:8080             │        │
│  │  vnc.example.com/gateway/* → controller:9000 │        │
│  │  Ports: 80 (HTTP→redirect), 443 (HTTPS)      │        │
│  └───────┬──────────────┬────────────┬──────────┘        │
│          │              │            │                    │
│  ┌───────▼──────┐ ┌────▼─────┐ ┌────▼──────────────┐    │
│  │  bridge      │ │  novnc   │ │ gateway-controller │    │
│  │  REST API    │ │  Browser │ │ Start/status via   │    │
│  │  Orders +    │ │  VNC GUI │ │ Docker socket      │    │
│  │  Trades      │ │          │ │                    │    │
│  └───────┬──────┘ └────┬─────┘ └────────────────────┘    │
│          │              │                                │
│  ┌───────▼──────────────▼──────┐                         │
│  │  ib-gateway                 │                         │
│  │  IBKR Gateway (Java)       │                         │
│  │  TWS API on ports 4003/4004│                         │
│  │  VNC on port 5900          │                         │
│  └─────────────────────────────┘                         │
│                                                          │
│  Firewall: SSH from deployer IP only                     │
│  HTTP/HTTPS open (Caddy auto-redirects HTTP → HTTPS)     │
└──────────────────────────────────────────────────────────┘
```

Five containers in a single Docker network:

- **`ib-gateway`** — [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php) running in Docker via [gnzsnz/ib-gateway](https://github.com/gnzsnz/ib-gateway). Exposes the TWS API (port 4003 live / 4004 paper) and VNC (port 5900) for 2FA authentication.
- **`bridge`** — Python aiohttp service that connects to the Gateway via [ib_async](https://github.com/ib-api-reloaded/ib_async) and exposes the REST API. Handles auto-reconnection with exponential backoff and a periodic watchdog.
- **`novnc`** — [noVNC](https://novnc.com/) browser-based VNC client for accessing the Gateway GUI. Used for 2FA authentication and monitoring.
- **`caddy`** — [Caddy 2](https://caddyserver.com/) reverse proxy with automatic HTTPS via Let's Encrypt. Routes API traffic to the bridge, VNC traffic to noVNC.
- **`gateway-controller`** — Lightweight CGI container with Docker socket access. Provides HTTP endpoints to start/check the Gateway container without SSH.

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
make setup        # Create .venv and install dependencies
cp .env.example .env
# Edit .env with your values

# 2. Deploy
make deploy

# 3. Complete 2FA
# Open https://vnc.example.com in your browser and authenticate

# 4. Verify connection
curl -s https://trade.example.com/health | python3 -m json.tool
# Should show: {"connected": true, "tradingMode": "paper"}

# 5. Place a test order (paper mode)
make order Q=1 SYM=AAPL T=MKT

# 6. Tear down when done
make destroy
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

All configuration is via environment variables in `.env`:

| Variable              | Required | Default              | Description                                                                                           |
| --------------------- | -------- | -------------------- | ----------------------------------------------------------------------------------------------------- |
| `DEPLOY_MODE`         | Yes      | —                    | `standalone` (own droplet via Terraform) or `shared` (deploy to existing droplet)                     |
| `DO_API_TOKEN`        | Yes\*    | —                    | DigitalOcean API token (standalone mode only — can be removed after first deploy)                     |
| `DROPLET_IP`          | Yes\*    | —                    | Droplet IP (from Terraform output in standalone; provided by host in shared)                          |
| `SSH_KEY`             | No       | `~/.ssh/ibkr-bridge` | SSH key path — **shared mode only**. In standalone, Terraform auto-generates the key; never set this. |
| `TWS_USERID`          | Yes      | —                    | IBKR username                                                                                         |
| `TWS_PASSWORD`        | Yes      | —                    | IBKR password                                                                                         |
| `VNC_SERVER_PASSWORD` | Yes      | —                    | Password for VNC access to the Gateway GUI                                                            |
| `TRADING_MODE`        | No       | `paper`              | `paper` or `live`                                                                                     |
| `VNC_DOMAIN`          | Yes      | —                    | Domain for VNC access (e.g. `vnc.example.com`)                                                        |
| `SITE_DOMAIN`         | Yes      | —                    | Domain for the REST API (e.g. `trade.example.com`)                                                    |
| `API_TOKEN`           | Yes      | —                    | Bearer token for `/ibkr/*` endpoints (`openssl rand -hex 32`)                                         |
| `JAVA_HEAP_SIZE`      | No       | `768`                | IB Gateway Java heap in MB. Determines auto-selected droplet size.                                    |
| `DROPLET_SIZE`        | No       | (auto)               | Override droplet size slug (e.g. `s-1vcpu-2gb`). When set, ignores `JAVA_HEAP_SIZE` for sizing.       |
| `TIME_ZONE`           | No       | `America/New_York`   | Timezone (tz database format)                                                                         |

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
  make sync        Push .env + restart (S=service B=1 LOCAL_FILES=1 ENV=local)
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

| Variable              | Service            | Command               |
| --------------------- | ------------------ | --------------------- |
| `API_TOKEN`           | bridge             | `make sync S=bridge`  |
| `TRADING_MODE`        | bridge, ib-gateway | `make sync`           |
| `TWS_USERID/PASSWORD` | ib-gateway         | `make sync S=gateway` |
| `VNC_SERVER_PASSWORD` | ib-gateway         | `make sync S=gateway` |
| `SITE_DOMAIN`         | caddy              | `make sync S=caddy`   |
| `VNC_DOMAIN`          | caddy              | `make sync S=caddy`   |
| Multiple or unsure    | all                | `make sync`           |

### Syncing code changes

#### Local stack

When `DEFAULT_CLI_BRIDGE_ENV=local` (or `ENV=local`), `make sync` restarts all containers. Bind mounts in `docker-compose.local.yml` ensure code changes are picked up automatically:

```bash
make sync              # restart (when DEFAULT_CLI_BRIDGE_ENV=local)
make sync ENV=local    # explicit override
```

#### Remote droplet

`make sync` only pushes `.env` and restarts containers. When you change Python code, Dockerfiles, or Compose config, use `LOCAL_FILES=1`:

```bash
make sync LOCAL_FILES=1
```

This runs a pre-deploy pipeline (branch check → clean tree → typecheck → tests → rsync → rebuild).

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

- Credentials live in `.env.test` (gitignored). Template: `.env.test.example`.
- `make e2e-run` restarts the `bridge` container to pick up code changes from volume mounts.
- Test bridge runs on `localhost:15010` with token `test-token`.
- Gateway startup takes 30–120 seconds (Java + IBKR authentication). The `e2e-up` target waits up to 240 seconds and detects session conflicts (another TWS/Gateway using the same credentials).

## TypeScript Types

API types are available as a TypeScript package under `types/`:

```
types/
  index.d.ts                 # Barrel: exports IbkrBridge namespace
  package.json               # @tradegist/ibkr-bridge-types
  http/
    index.d.ts               # Re-exports all types
    types.d.ts               # Generated from bridge_models.py SCHEMA_MODELS
    types.schema.json         # Intermediate JSON Schema
```

Usage:

```typescript
import { IbkrBridge } from "@tradegist/ibkr-bridge-types";

const req: IbkrBridge.PlaceOrderPayload = {
  contract: { symbol: "AAPL", secType: "STK", exchange: "SMART", currency: "USD" },
  order: { action: "BUY", totalQuantity: 10, orderType: "MKT" },
};

const resp: IbkrBridge.PlaceOrderResponse = ...;
const trades: IbkrBridge.ListTradesResponse = ...;
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
├── .env.example                   # Configuration template
├── docker-compose.yml             # Container orchestration (5 services)
├── docker-compose.shared.yml      # Shared-mode overlay (disables Caddy, uses relay-net)
├── docker-compose.local.yml       # Local dev override (direct port access, no TLS)
├── docker-compose.test.yml        # E2E test override (paper mode, no Caddy/noVNC)
├── services/
│   └── bridge/
│       ├── Dockerfile             # Python 3.11-slim
│       ├── requirements.txt       # ib_async, aiohttp, httpx, pydantic
│       ├── main.py                # Entrypoint (IB connection + HTTP server)
│       ├── bridge_models.py       # Pydantic models (request/response types)
│       ├── client/                # IB Gateway client
│       │   ├── __init__.py        # IBClient (connection, reconnection, watchdog)
│       │   ├── orders.py          # OrdersNamespace (place orders)
│       │   └── trades.py          # TradesNamespace (list trades + fills)
│       ├── bridge_routes/         # HTTP API
│       │   ├── __init__.py        # Route orchestrator (create_routes)
│       │   ├── health.py          # GET /health handler
│       │   ├── middlewares.py     # Auth middleware (Bearer token, HMAC-safe)
│       │   ├── order_place.py     # POST /ibkr/order handler
│       │   └── trades_list.py     # GET /ibkr/trades handler
│       └── tests/e2e/             # E2E tests
│           ├── conftest.py        # httpx fixtures (api + anon_api)
│           └── test_smoke.py      # Health + auth smoke tests
├── infra/
│   ├── caddy/
│   │   ├── Caddyfile              # Reverse proxy config (SITE_DOMAIN + VNC_DOMAIN)
│   │   ├── docker-entrypoint.sh   # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH
│   │   ├── sites/
│   │   │   └── ibkr-bridge.caddy  # SITE_DOMAIN API routes (/ibkr/order, /ibkr/trades, /health)
│   │   └── domains/
│   │       └── ibkr-vnc.caddy    # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
│   ├── gateway-controller/
│   │   ├── Dockerfile             # Alpine + docker CLI for container control
│   │   ├── start-gateway.sh       # CGI: start ib-gateway container
│   │   └── gateway-status.sh      # CGI: check ib-gateway status
│   └── novnc/
│       └── index.html             # Custom noVNC landing page
├── terraform/
│   ├── main.tf                    # Droplet, firewall, reserved IP, SSH key
│   ├── variables.tf               # Terraform variables
│   ├── outputs.tf                 # Droplet IP, VNC URL, Site URL, SSH key
│   └── cloud-init.sh             # Docker install + project directory
├── schema_gen.py                  # JSON Schema generator (Pydantic → TS types)
└── types/                         # @tradegist/ibkr-bridge-types npm package
    ├── index.d.ts                 # Barrel: exports IbkrBridge namespace
    ├── package.json
    └── http/                      # IbkrBridge namespace
        ├── index.d.ts
        └── types.d.ts             # Generated from bridge_models.py SCHEMA_MODELS
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
- [ ] Cancel order endpoint
- [ ] Health monitoring / alerting
