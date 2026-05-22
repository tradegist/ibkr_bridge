# `services/bridge/` — The IB Gateway bridge service

The only service in this project. Connects to IB Gateway via `ib_async`, exposes REST + WebSocket endpoints under `/ibkr/*`.

For test conventions and the model-layout rule, see [services/CLAUDE.md](../CLAUDE.md). For the file tree and event-wiring narrative, see [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).

## Auth Pattern

- API endpoints under `/ibkr/*` require `Authorization: Bearer <API_TOKEN>` (HMAC-safe comparison via `hmac.compare_digest`).
- **All authenticated routes must use the `AUTH_PREFIX` constant** (from `bridge_routes.constants`) when registering with the router. The auth middleware uses the same constant to decide which requests require a token — hardcoding the path in either place causes them to drift.
- `AUTH_PREFIX` is currently `"/ibkr"`. The validation pattern is `path.startswith(f"{prefix}/")`, so a single `/ibkr` prefix covers `/ibkr/order`, `/ibkr/trades`, `/ibkr/ws/events`, etc.
- `/health` is unauthenticated — used for monitoring and load-balancer checks.

## IB Gateway connection (MANDATORY behaviour)

- **The HTTP server starts before the IB connection.** `main.py` binds the aiohttp server **first**, then calls `client.connect()`. This ensures `/health` is reachable (returning `connected: false`) while the Gateway is down or during reconnection. Handlers return 503 when `client.is_connected` is `False`.
- **The `IBClient` class manages the connection lifecycle.** It connects with exponential backoff (`INITIAL_RETRY_DELAY=10` to `MAX_RETRY_DELAY=300`), auto-reconnects on disconnect via the `disconnectedEvent` callback, and runs a 30-second watchdog loop.
- **Trading mode** is determined by `TRADING_MODE` env var (`paper` or `live`). Paper uses port 4004, live uses port 4003.
- **Client ID is hardcoded to 1.** Only one `IBClient` instance connects to the Gateway at a time.
- **Namespace delegation.** Orders and trades are separated into `OrdersNamespace` (`client/orders.py`) and `TradesNamespace` (`client/trades.py`), each receiving the `ib_async.IB` instance. Domain logic is isolated from connection management.

## Event wiring

- **`IBClient.subscribe_events()`** registers four `ib_async` callbacks on the `IB` object: `execDetailsEvent`, `commissionReportEvent`, `positionEvent`, and `connectedEvent`. All survive reconnects because they live on the `IB` object (created once in `__init__`, only the wrapper state is reset on disconnect).
- **Subscribe BEFORE `client.connect()`** so `connectedEvent` fires for the very first connection.

## Cross-user fill reconciliation (MANDATORY safeguards)

`ib_async` only emits `execDetailsEvent` for **live** fills (`isLive=True` gate in `wrapper.execDetails`) and only emits `commissionReportEvent` when the trade is in `permId2Trade` — neither is true for orders placed by a *different* IBKR user on the same account. To surface those fills, `_on_position` schedules `_reconcile_executions`, which calls `reqExecutionsAsync()` and **manually broadcasts** each returned `Fill` as `commissionReportEvent` with `source="reconciled"`.

- **Concurrent positionEvents are coalesced** — single in-flight task.
- **Initial-sync gate** (`INITIAL_SYNC_GRACE_SECONDS=1.0`, armed by `connectedEvent`) suppresses reconciles during the post-connect position flood. The pending `_mark_synced` timer is tracked on `self._sync_timer` and **cancelled on every reconnect** so a rapid disconnect/reconnect inside the grace window doesn't let a stale timer open the gate during the next connection's position flood.
- **Pre-fetch settle delay** (`RECONCILE_SETTLE_SECONDS=1.0`) lets the matching commission report land on the same `Fill` before reading.
- **Per-fill failure isolation.** `_reconcile_executions` wraps each `_broadcast_fill` call in its own try/except — a single malformed payload doesn't abort the rest of the batch. **An execId is added to `_broadcast_exec_ids` ONLY after a successful broadcast** so a fill that raised is eligible for retry on the next reconcile. Failures are surfaced via `log.exception` and counted in the per-reconcile INFO summary (`reconcile: N new fill(s) broadcast (M already seen, K failed)`).

## execId dedupe map (MANDATORY invariants)

`IBClient._broadcast_exec_ids: dict[str, datetime]` maps every broadcast `commissionReportEvent` execId to its fill timestamp.

- **Both the live and reconcile paths gate on this map** — `_on_commission_report` checks the map first and drops duplicates (so a slow live commissionReport arriving *after* reconcile won the race doesn't double-emit).
- **`execDetailsEvent` deliberately does NOT populate the map** — only commissionReport gates reconcile. Handles the edge case where the live commissionReport callback never fires (e.g., `permId2Trade` gate fails for completed external orders): the reconcile path can still fill the gap with full commission data.
- **The map is NOT cleared on reconnect** — a transient same-day reconnect must not re-emit fills as `source="reconciled"`. IB execIds are globally unique (the prefix encodes day/account/client), so stale entries from prior IB sessions cannot collide with new fills.
- **`_prune_stale_exec_ids` runs at the start of every reconcile** and drops entries older than `DEDUPE_RETENTION` (2 days). The two-day window safely spans any IBKR session boundary, weekend, or holiday gap. Memory ceiling: ~`fills_per_day × 2` entries (~80 bytes each).
- **`_record_broadcast` normalises stored timestamps to tz-aware UTC** in all three input shapes (None → `datetime.now(UTC)`; naive → `replace(tzinfo=UTC)`; tz-aware → as-is) so the prune's `t < cutoff` comparison cannot raise `TypeError: can't compare offset-naive and offset-aware datetimes`.

## WebSocket Event Streaming

- **`GET /ibkr/ws/events`** upgrades to WebSocket and streams real-time execution events to subscribers.
- **Auth** uses the same `auth_middleware` — the path starts with `/ibkr/`, so `Authorization: Bearer <API_TOKEN>` is required in the upgrade request headers.
- **`EventHub`** (`client/event_hub.py`) is the pub/sub core:
  - Global ring buffer (`collections.deque`) stores last `WS_BUFFER_SIZE` events (default 500).
  - Each subscriber gets an `asyncio.Queue` for delivery.
  - `broadcast()` assigns a monotonic `seq`, appends to buffer, and pushes to all subscriber queues.
  - `replay(from_seq)` returns buffered events with `seq > from_seq`.
- **Message format**: `WsEnvelope` is a `TypeAlias` discriminated union over `type`:
  - `WsStatusEnvelope` (`type`, `seq`, `timestamp`) for `connected` / `disconnected` (no `source` field).
  - `WsFillEnvelope` (`type`, `seq`, `timestamp`, `fill`, `source`) for `execDetailsEvent` / `commissionReportEvent`. `source` is `"live"` for push callbacks or `"reconciled"` for the positionEvent → reqExecutions path.
- **Python validation**: consumers validating raw dicts must use `TypeAdapter(WsEnvelope).validate_python(data)` — calling `WsEnvelope.model_validate(...)` will not work because `WsEnvelope` is a TypeAlias, not a class. TypeScript narrowing on `type` gives full type safety on the branches.
- **Zombie detection**: `WebSocketResponse(heartbeat=WS_HEARTBEAT_INTERVAL)` sends pings; aiohttp auto-closes unresponsive connections. Cleanup runs in `try/finally` to unsubscribe.
- **Max subscribers**: `WS_MAX_SUBSCRIBERS` (default 10). Exceeding returns WS close code 4029.
- **Reconnect replay**: client passes `?last_seq=N` to receive missed events from the ring buffer.
- **No new port needed**: WebSocket runs on the same aiohttp server (port 5000). Caddy proxies it transparently — no special upgrade config needed.

## Bridge Structure constraints

- **`services/bridge/client/`** owns IB Gateway client logic. `IBClient` is the connection lifecycle manager; `OrdersNamespace` and `TradesNamespace` handle domain logic; `EventHub` manages pub/sub for WS subscribers. Tests are colocated (`test_event_hub.py`, `test_orders.py`, `test_trades.py`, `test_reconcile.py`).
- **`services/bridge/bridge_routes/`** owns the HTTP/WS API. `constants.py` defines `AUTH_PREFIX`, `client_key`, and `hub_key` — handler modules import these rather than re-declaring them.
- **`services/bridge/bridge_models.py`** is the single public type surface. Every public Pydantic model and Literal alias used by the HTTP API or WS events lives here. Internal-only helpers do not belong in this file.
- **WS event models mirror `ib_async` 2.1.0 exactly** — same field names, same nesting (`WsFill.contract`, `WsFill.execution`, `WsFill.commissionReport`). When bumping `ib_async`, update these models to match (see the `bump-ib-async-version` skill).
- `WsEnvelope` is a discriminated union (TypeAlias). The narrowed Python literals (`WsStatusType`, `WsFillEventType`) are private to `client/__init__.py` — do not export them.
