# IBKR Bridge — Architecture

Descriptive overview of how the system fits together. **Enforceable rules** live in `CLAUDE.md` files (root + per-directory) and their Copilot mirrors. This document is reference material for humans and for Claude when asked architectural questions — it is not auto-loaded into every conversation.

## System overview

Six Docker containers in a single Compose stack on a DigitalOcean droplet:

| Service | Role |
| --- | --- |
| `ib-gateway` | IB Gateway (Java) — TWS API on 4003/4004, VNC on 5900. Image: `ghcr.io/gnzsnz/ib-gateway`. `restart: unless-stopped`. `AUTO_RESTART_TIME` (default `11:30 PM`) — IBC proactively restarts before IBKR's nightly ~11:45 PM ET session reset, writing an autorestart file so the next start skips 2FA. After the weekly session expiry, the container exits needing 2FA; `monitor-gateway` stops it (breaking the restart loop) and sends an alert. Healthcheck uses an RFB banner probe (reads `RFB 003.xxx` from port 5900) rather than a TCP-only check — x11vnc accumulates CLOSE_WAIT sockets and accepts TCP connections while being unable to serve clients. |
| `bridge` | Python REST/WS API — connects to Gateway via `ib_async`, exposes `/ibkr/order`, `/ibkr/trades`, `/ibkr/ws/events`, `/health`. |
| `novnc` | Browser-based VNC client for 2FA and Gateway monitoring. RFB-banner health check. Browser auto-retries on disconnect. |
| `caddy` | Reverse proxy with automatic HTTPS (Let's Encrypt). Routes API and VNC traffic. |
| `gateway-controller` | Alpine + Docker CLI — HTTP endpoints to start/check the Gateway via Docker socket. Runs a background `monitor-gateway` daemon that sends Resend email alerts on unexpected ib-gateway exits. On 2FA exits, stops the restarted container (breaking `restart: unless-stopped`) and sends a "needs 2FA" alert. |
| `autoheal` | Watches containers labelled `autoheal=true` and restarts them when unhealthy. Restarts `novnc` when VNC backend is unreachable. |

All secrets are injected via `.env` → `environment:` in `docker-compose.yml`. Caddy reads `SITE_DOMAIN` and `VNC_DOMAIN` from env vars.

## Project file structure

```
env_examples/                      # Env var templates used by setup
  env                              # → .env (app config; `make setup` copies this file)
  env.droplet                      # → .env.droplet (CLI-only config)
  env.test                         # → .env.test (E2E test config)
docker-compose.yml                 # All services (ib-gateway, bridge, novnc, caddy, gateway-controller, autoheal)
docker-compose.shared.yml          # Shared-mode overlay (disables Caddy)
docker-compose.shared-network.yml  # Marks SHARED_NETWORK as external
docker-compose.local.yml           # Local dev override (direct port access, no TLS)
docker-compose.test.yml            # Test stack override (paper mode, no Caddy/noVNC)
cli/
  __init__.py                      # Project-specific CoreConfig, helpers, bridge_api
  __main__.py                      # Entry point (lazy dispatch via importlib)
  order.py                         # Place an order via CLI
  core/                            # Project-agnostic (reusable across projects)
    deploy.py destroy.py pause.py resume.py sync.py
services/
  shared/                          # Reserved for future cross-project shared types
    __init__.py                    # Comment-only stub; currently no models
  bridge/                          # REST + WS API service (the only service)
    main.py                        # Entrypoint (IB connection + HTTP server)
    bridge_models.py               # Pydantic models + Literal aliases (single source of truth)
    client/                        # IB Gateway client package
      __init__.py                  # IBClient (connection, reconnect, watchdog, event wiring, reconcile)
      event_hub.py                 # EventHub (pub/sub + ring buffer for WS replay)
      orders.py                    # OrdersNamespace (place orders)
      trades.py                    # TradesNamespace (list trades + fills)
    bridge_routes/                 # HTTP + WS API
      __init__.py                  # Route orchestrator (create_routes)
      constants.py                 # AUTH_PREFIX, client_key, hub_key (aiohttp AppKeys)
      health.py                    # GET /health
      middlewares.py               # Auth middleware (Bearer, HMAC-safe)
      order_place.py               # POST /ibkr/order
      trades_list.py               # GET /ibkr/trades
      ws_events.py                 # GET /ibkr/ws/events
    tests/e2e/                     # E2E tests (require Docker stack)
      conftest.py                  # httpx fixtures + preflight check
      test_smoke.py
    Dockerfile
    requirements.txt
infra/
  caddy/
    Caddyfile                      # Reverse proxy config (SITE_DOMAIN + VNC_DOMAIN)
    docker-entrypoint.sh           # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH, starts Caddy
    sites/
      ibkr-bridge.caddy            # SITE_DOMAIN API routes
    domains/
      ibkr-vnc.caddy               # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
  gateway-controller/              # CGI container for Gateway lifecycle + crash monitor
    Dockerfile
    entrypoint.sh                  # Starts monitor-gateway in background, then execs httpd
    start-gateway.sh               # CGI: start ib-gateway container
    gateway-status.sh              # CGI: check ib-gateway status
    monitor-gateway.sh             # Background daemon: watches docker events, sends Resend alerts
  novnc/
    index.html                     # Custom noVNC landing page
terraform/
  main.tf variables.tf outputs.tf cloud-init.sh
schema_gen.py                      # JSON Schema generator (Pydantic → JSON Schema)
gen_ts_barrels.py                  # TS barrel generator (parses types.d.ts → index.d.ts)
gen_python_types.py                # Python types generator (bridge_models.py → models.py + __init__.py)
types/
  typescript/                      # @tradegist/ibkr-bridge-types npm package
    index.d.ts                     # Hand-maintained barrel (exports IbkrBridgeHttp namespace)
    package.json
    http/
      index.d.ts                   # Auto-generated barrel
      types.d.ts                   # Auto-generated from bridge_models.py SCHEMA_MODELS
      types.schema.json            # Intermediate JSON Schema
  python/                          # ibkr-bridge-types PyPI package
    pyproject.toml
    ibkr_bridge_types/
      __init__.py                  # Auto-generated barrel
      models.py                    # Auto-generated from bridge_models.py
docs/
  runbooks/
    vnc-pending.md
  ARCHITECTURE.md                  # This file
  INSTRUCTION_FILES.md             # Sync contract for CLAUDE.md ↔ Copilot mirrors
```

## Bridge service internals

- **`services/bridge/main.py`** — binds the aiohttp HTTP server **before** calling `client.connect()`. The order matters: `/health` is reachable while the Gateway is down or during reconnection.
- **`services/bridge/client/__init__.py`** — `IBClient` owns connection lifecycle (exponential backoff `INITIAL_RETRY_DELAY=10` to `MAX_RETRY_DELAY=300`, auto-reconnect via `disconnectedEvent`, 30-second watchdog) and the `_broadcast_exec_ids: dict[str, datetime]` dedupe map.
- **`services/bridge/client/event_hub.py`** — pub/sub: ring buffer (`collections.deque`) of last `WS_BUFFER_SIZE` events (default 500), per-subscriber `asyncio.Queue`, `broadcast()` assigns monotonic `seq`, `replay(from_seq)` returns events with `seq > from_seq`.
- **`services/bridge/client/orders.py`** + **`services/bridge/client/trades.py`** — domain namespaces. Each receives the `ib_async.IB` instance, keeping domain logic isolated from connection management.
- **`services/bridge/bridge_routes/`** — HTTP API. `constants.py` defines `AUTH_PREFIX = "/ibkr"`, plus `client_key` and `hub_key` (`aiohttp.web.AppKey`) for the shared `IBClient` and `EventHub`. Middleware uses `AUTH_PREFIX` to decide which requests need a Bearer token.
- **`services/bridge/bridge_models.py`** — single source of truth for all public Pydantic models and `Literal` type aliases (`Action`, `OrderType`, `SecType`, `TimeInForce`, `ExecSide`). Every type listed in `schema_gen.py:SCHEMA_MODELS` is regenerated to TS + Python type packages via `make types`.

## IB Gateway event wiring

Before `client.connect()`, `IBClient.subscribe_events()` registers four `ib_async` callbacks on the `IB` object:

- `execDetailsEvent` — live same-user fills.
- `commissionReportEvent` — live commission reports (matches an `execDetails` by `permId`).
- `positionEvent` — triggers cross-user reconcile.
- `connectedEvent` — arms the initial-sync gate.

All survive reconnects because they live on the `IB` object (created once in `__init__`).

### Cross-user fill reconciliation

`ib_async` only emits `execDetailsEvent` for **live** fills (`isLive=True` gate in `wrapper.execDetails`) and only emits `commissionReportEvent` when the trade is in `permId2Trade`. Neither is true for orders placed by a *different* IBKR user on the same account (e.g. mobile login as User B while the bridge runs as User A).

To surface those fills, `_on_position` schedules `_reconcile_executions`, which calls `reqExecutionsAsync()` and **manually broadcasts** each returned `Fill` as `commissionReportEvent` with `source="reconciled"`. Key safeguards:

- **Concurrent positionEvents are coalesced** (single in-flight task).
- **Initial-sync gate** (`INITIAL_SYNC_GRACE_SECONDS=1.0`, armed by `connectedEvent`) suppresses reconciles during the post-connect position flood. The pending `_mark_synced` timer is tracked on `self._sync_timer` and **cancelled on every reconnect** so a rapid disconnect/reconnect inside the grace window doesn't let a stale timer open the gate during the next connection's position flood.
- **Settle delay** (`RECONCILE_SETTLE_SECONDS=1.0`) lets the matching commission report land on the same `Fill` before reading.
- **Per-fill failure isolation** — each `_broadcast_fill` call is wrapped in its own try/except. An execId is added to `_broadcast_exec_ids` **only after a successful broadcast**, so a fill that raised is eligible for retry on the next reconcile.

### execId dedupe map

`IBClient._broadcast_exec_ids: dict[str, datetime]` maps every broadcast `commissionReportEvent` execId to its fill timestamp.

- **Both the live and reconcile paths gate on this map** — `_on_commission_report` checks first and drops duplicates (a slow live commissionReport arriving after reconcile won the race won't double-emit).
- **`execDetailsEvent` deliberately does NOT populate the map** — only commissionReport gates reconcile. This handles the edge case where the live commissionReport callback never fires (e.g. `permId2Trade` gate fails for completed external orders): reconcile is still free to fill the gap.
- **The map is NOT cleared on reconnect** — a transient same-day reconnect must not re-emit fills as `source="reconciled"`. IB execIds are globally unique (the prefix encodes day/account/client), so stale entries from prior IB sessions cannot collide.

### Bounded memory

`_prune_stale_exec_ids` runs at the start of every reconcile and drops entries older than `DEDUPE_RETENTION` (2 days). Memory ceiling: ~`fills_per_day × 2` entries (~80 bytes each). `_record_broadcast` normalises stored timestamps to **tz-aware UTC** so the prune's `t < cutoff` comparison cannot raise `TypeError: can't compare offset-naive and offset-aware datetimes`.

### WS event format

Every WS event uses `WsEnvelope` — a discriminated union (TypeAlias) over the `type` field:

- `WsStatusEnvelope` (`type`, `seq`, `timestamp`) for `connected` / `disconnected`. No `source` field.
- `WsFillEnvelope` (`type`, `seq`, `timestamp`, `fill`, `source`) for `execDetailsEvent` / `commissionReportEvent`. `source` is `"live"` for push callbacks or `"reconciled"` for the positionEvent → reqExecutions path.

In Python, `WsEnvelope` is a `TypeAlias` (not a class). Consumers validating raw dicts must use `TypeAdapter(WsEnvelope).validate_python(data)` — `WsEnvelope.model_validate(...)` will not work. In TypeScript, narrowing on `type` gives full type safety.

## Models — two locations

| File | Domain | Status |
| --- | --- | --- |
| `services/shared/__init__.py` | Cross-project shared types (future `IbkrBridge` TS namespace) | Reserved; currently no models defined |
| `services/bridge/bridge_models.py` | All bridge HTTP + WS types (`IbkrBridgeHttp` TS namespace) | Single source of truth |

When shared types are added to `services/shared/`, register them under a new `"shared"` entry in `schema_gen.py:SCHEMA_MODELS`, create `types/typescript/shared/`, and update the hand-maintained `types/typescript/index.d.ts` barrel to export `IbkrBridge`.

## Public model inventory

Generated under the `IbkrBridgeHttp` namespace:

| Model | Direction | Description |
| --- | --- | --- |
| `PlaceOrderPayload` | Inbound | `POST /ibkr/order` request body (contract + order) |
| `ContractPayload` | Inbound | Contract fields (symbol, secType, exchange, currency) |
| `OrderPayload` | Inbound | Order fields (action, qty, type, price, tif) |
| `PlaceOrderResponse` | Outbound | Order placement result (status, orderId, etc.) |
| `HealthResponse` | Outbound | `GET /health` response |
| `ListTradesResponse` | Outbound | `GET /ibkr/trades` response (array of TradeDetail) |
| `TradeDetail` | Outbound | Order + status + fills |
| `FillDetail` | Outbound | Single execution fill within a trade |
| `WsEnvelope` | Outbound | Discriminated union: `WsStatusEnvelope \| WsFillEnvelope` |
| `WsStatusEnvelope` | Outbound | Connection status (`type`, `seq`, `timestamp`) |
| `WsFillEnvelope` | Outbound | Fill event (`type`, `seq`, `timestamp`, `fill`, `source`) |
| `WsFill` | Outbound | Fill payload (contract + execution + commissionReport) |
| `WsContract` | Outbound | Mirrors `ib_async.Contract` (2.1.0) |
| `WsExecution` | Outbound | Mirrors `ib_async.Execution` (2.1.0) |
| `WsCommissionReport` | Outbound | Mirrors `ib_async.CommissionReport` (2.1.0) |
| `WsComboLeg` | Outbound | Mirrors `ib_async.ComboLeg` (2.1.0) |
| `WsDeltaNeutralContract` | Outbound | Mirrors `ib_async.DeltaNeutralContract` (2.1.0) |

Literal type aliases exported alongside the models: `Action`, `ExecSide`, `OrderType`, `SecType`, `TimeInForce`, `WsEventType`, `WsEventSource`.

Note: `TradeDetail.action` and `TradeDetail.orderType` are `str` (not the constrained Literals) because IB Gateway returns values beyond our aliases for existing orders (`STP`, `TRAIL`, etc.).

## Deployment modes

Controlled by `DEPLOY_MODE` in `.env`:

- **Standalone** — Terraform creates a fresh droplet + firewall + reserved IP; CLI rsyncs + pushes `.env` + brings the stack up.
- **Shared** — multiple projects share a single droplet and one Caddy. Set `SHARED_NETWORK` in `.env`. CLI applies `docker-compose.shared.yml` (disables Caddy) + `docker-compose.shared-network.yml` (joins the external network).

See [cli/CLAUDE.md](../cli/CLAUDE.md) for the full deploy/sync rules and rsync invariants.

## Authentication

- `/ibkr/*` endpoints require `Authorization: Bearer <API_TOKEN>` (HMAC-safe via `hmac.compare_digest`).
- `/health` is unauthenticated — used for monitoring and load-balancer checks.
- WebSocket: `GET /ibkr/ws/events` uses the same auth (path matches `AUTH_PREFIX`); pass the bearer token in the upgrade request headers.

## Local development

- `.venv` is the project's virtual environment (`make setup` creates it from Homebrew Python).
- `ibkr-bridge.pth` adds `services/bridge/` to `sys.path` so `from bridge_models import ...`, `from client import ...`, `from bridge_routes import ...` work without `PYTHONPATH`.
- `docker-compose.local.yml` adds `:ro` bind mounts so local source shadows the image's COPY'd files — code changes are visible on container restart, no rebuild.
- `DEFAULT_CLI_ENV` in `.env.droplet` selects local vs prod for `make sync` and `make logs`.

## Auto-loaded vs on-demand instructions (this repo's setup)

This repo splits AI instruction files into three layers:

| Layer | Location | When loaded by Claude |
| --- | --- | --- |
| **Always-on rules** | Root `CLAUDE.md` | Every session |
| **Directory-scoped rules** | `<dir>/CLAUDE.md` | On demand, when Claude reads a file in that subtree |
| **Playbooks** | `.claude/skills/<name>/SKILL.md` | Only when the skill is invoked |
| **Architecture prose** (this doc) | `docs/ARCHITECTURE.md` | Not auto-loaded — read on demand via Read/Grep |

The same split is mirrored to GitHub Copilot via `.github/copilot-instructions.md` (universal) and `.github/instructions/*.instructions.md` (path-scoped with `applyTo:` frontmatter). See [docs/INSTRUCTION_FILES.md](INSTRUCTION_FILES.md) for the maintenance contract.
