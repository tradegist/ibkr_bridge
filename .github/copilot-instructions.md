# IBKR Bridge — Project Guidelines

## Sibling Project: relayport

This project (`ibkr_bridge`) and its sibling project `relayport` share the same CLI deploy/destroy/sync infrastructure pattern. **Any change to `cli/core/deploy.py`, `cli/core/destroy.py`, or `cli/core/sync.py` in this project must be mirrored in the sibling project, and vice versa.** This includes: Terraform state management, reserved IP handling, rsync exclusions, env file push logic, and compose startup commands. When you modify CLI core logic here, explicitly remind the user to apply the equivalent change to `relayport`, and offer to do it in the same session.

## Code Quality (MANDATORY)

- **Always apply best practices by default.** Do not ask the user whether to follow a best practice — just do it. Use idiomatic Python naming, file organization, and patterns. When there is a clearly better approach (naming, structure, error handling), use it directly and explain why.
- **No unused imports.** After writing or editing any Python file, verify every `import` is actually used in the file. Remove any that are not. This applies to new files and edits to existing files alike.
- **No `__all__`.** All imports are explicit (`from module import X`). `__all__` only controls star-imports, which we never use.
- **No `assert` for runtime guards.** `assert` is stripped under `python -O`, turning invariant checks into silent `None`/`AttributeError`. Use `if ... raise RuntimeError(...)` (or `die()`) for any check that must hold at runtime.
- **Makefile must mirror CLI arguments.** When adding a new parameter to a `cli/` command, always add the corresponding `$(if $(VAR),--flag $(VAR))` to the Makefile target so `make <target> VAR=value` works. **CLI parameters that are optional in the Makefile must be named flags (`--currency`, `--exchange`), never positional args.** When the Makefile uses `$(if $(VAR),...)`, omitting `VAR` omits the entire argument — if the CLI parameter is positional, downstream args shift into the wrong position and get silently misparsed.
- **Update README.md when changing public interfaces.** When adding or modifying CLI commands, Makefile targets, API endpoints, or env vars, always update the README to reflect the change.
- **Run `make lint` after every code change.** Ruff enforces unused imports (F401), import ordering (I001), unused variables, common pitfalls (bugbear), and modern Python idioms. If ruff fails, fix before committing. Use `make lint FIX=1` to auto-fix safe issues (import sorting, etc.).
- **Register new modules in `pyproject.toml`.** When adding a new Python package or standalone module under `services/` or `types/python/`, immediately add it to `pyproject.toml`: (1) `tool.pytest.ini_options.testpaths` (if it has tests), (2) `tool.ruff.src`, (3) `tool.ruff.lint.isort.known-first-party`, and (4) the mypy invocation in the Makefile. Also add it to the ruff and mypy paths in the Makefile `lint:` and `typecheck:` targets. Missing any of these causes silent miscategorisation (isort), missed tests (pytest), or unchecked code (mypy).
- **Centralise env var reads into typed getter functions.** Each env var must be read in exactly one place — a getter function in the module that owns it (e.g. `get_ib_host()` in `client/__init__.py`). The getter applies `.strip()` and any type conversion (`int()`, boolean parsing). All other code imports and calls the getter. Never call `os.environ.get()` inline except inside a getter.
- **Getters must validate and fail fast.** Every getter that reads an env var must validate the value and raise `SystemExit` with a descriptive message on invalid input. For required string vars, check emptiness. For `int()` conversions, wrap in `try/except ValueError: raise SystemExit(...)`. Callers should never need to validate a getter's return value.
- **Prefer pure functions over side-effect functions.** Compute and return values — let the caller decide how to use them. If a side-effect function is truly unavoidable, add an inline comment at every call site explaining **what** is mutated and **why**.
- **Never bulk-set `os.environ` with empty-string fallbacks.** A loop like `os.environ[key] = env(name, "")` silently overrides downstream defaults (e.g. Terraform `variable` defaults, library config) with empty strings — the downstream system sees the variable as _set but empty_ instead of _unset_, which breaks `tonumber()`, validation blocks, and non-string parsing. When bridging env vars to another system (Terraform `TF_VAR_*`, subprocess env, etc.), only export a key when the source value is present and non-empty. Explicitly `os.environ.pop(key, None)` otherwise so stale values from a previous run don't leak through.

## Security Rules (MANDATORY)

- **No hardcoded credentials** — passwords, API tokens, secrets, and keys MUST come from environment variables (`.env` file or `TF_VAR_*`). Never write real values in source files.
- **No hardcoded IPs** — use `DROPLET_IP` from `.env.droplet`. In documentation, use `1.2.3.4` as placeholder.
- **No hardcoded domains** — use `example.com` variants (`trade.example.com`, `vnc.example.com`) in docs and code. Actual domains are loaded at runtime via `SITE_DOMAIN` and `VNC_DOMAIN` env vars.
- **No email addresses or personal info** — never write real names, emails, or account IDs in committed files.
- **No developer-machine paths** — never write absolute paths like `/Users/john/...` or `C:\Users\john\...` in any committed file (docs, instructions, configs, comments). These leak personal and machine-specific information into a public repo. Reference sibling projects by name only, never by local filesystem path.
- **No logging of secrets or sensitive operational data** — never `log.info()` or `print()` tokens, passwords, or API keys. Log actions and outcomes, not credential values. Prefer logging symbols, statuses, and counts over full objects.
- **`.env`, `*.tfvars`, and `.env.test` are gitignored** — never commit them. Use `.env.example` / `.env.test.example` with placeholder values as reference.
- **Terraform state is gitignored** — `terraform.tfstate` contains SSH keys and IPs. Never commit it.
- **Auth middleware must reject empty `API_TOKEN`.** `hmac.compare_digest("", "")` returns `True`, so an empty `API_TOKEN` env var silently disables authentication. The auth middleware checks `if not api_token:` and returns HTTP 500 **before** reaching `compare_digest`. `API_TOKEN` is in `required_env` for deploy/sync — the CLI will block deployment if it is missing or empty.

## Type Safety (MANDATORY)

- **Python >= 3.11 is required.** The project uses `X | None` union syntax natively (no `from __future__ import annotations`). Docker images use `python:3.11-slim`. Local dev uses a `.venv` created from the latest Homebrew Python.
- **Run `make typecheck` before copying ANY Python file to the droplet.** This is non-negotiable. If mypy fails, do NOT push the code.
- **Run `make test` before assuming work is done and before copying ANY file to the droplet.** If tests fail, fix them first. Never deploy untested code.
- **Run `make test` and `make typecheck` after every code change**, even refactors. Do not wait until the end — verify immediately.
- **Run E2E tests after modifying any E2E test OR infrastructure file.** Infrastructure files include `docker-compose*.yml`, `Dockerfile`, `Caddyfile`, and anything under `infra/`. E2E tests require the Docker stack with IB Gateway — `make test` (unit tests) does not run them. Never assume an E2E test passes without actually running the stack. The E2E workflow is:
  1. `make e2e-up` — start the stack (idempotent, waits up to 240s for Gateway connection).
  2. `make e2e-run` — run the tests.
  3. Fix code → `make e2e-run` → repeat until all tests pass. Volume mounts keep code in sync — no rebuild needed.
  4. `make e2e-down` — tear down **only after all tests pass**. Never tear down between iterations.
- When modifying any Python file (`.py`), always run `make test`, `make typecheck`, and `make lint` and confirm all pass before deploying.
- **Every Python file must be covered by `make typecheck`.** When adding a new Python package or script, immediately add it to the mypy invocation in the Makefile.
- After modifying any model in `services/bridge/bridge_models.py`, also run `make types` to regenerate the TypeScript and Python type packages.
- **Always verify type safety by breaking it first.** After any refactor that touches types or model construction, deliberately introduce a type error, run `make typecheck`, and confirm it **fails**. Then revert and confirm it passes.
- **Avoid `dict[str, Any]` round-trips.** Never use `model_dump()` → `dict` → `Model(**data)` — mypy cannot type-check `**dict[str, Any]`. Use explicit keyword arguments or `model_copy(update=...)` instead.
- **Prefer strict `Literal` types over bare `str` on Pydantic models.** Financial applications demand precision. When a field has a known set of valid values (e.g. `Action`, `OrderType`, `SecType`, `TimeInForce`, `ExecSide`), always use the existing `Literal` type alias. Only fall back to `str` when the external source (e.g. IB Gateway) genuinely returns unbounded values — and document why with an inline comment (see `TradeDetail.action` and `TradeDetail.orderType` for examples).
- **No `# type: ignore` without justification.** Fix the root cause instead. If suppression is truly unavoidable (e.g. untyped `ib_async` attributes), the comment must include a reason: `# type: ignore[attr-defined] # ib_async.Foo has no stubs`. A bare `# type: ignore` with no explanation is never acceptable.
- **Use `cast()` instead of `# type: ignore[arg-type]`.** When passing a mock or compatible object where mypy expects a concrete type (e.g. `IBClient`), use `cast(IBClient, mock)` — not `# type: ignore[arg-type]`. This applies everywhere: test code, adapters, and third-party library wrappers. `cast()` is a documented assertion that preserves type-checking downstream; `# type: ignore` silently disables it.
- **Use `cast()` for ib_async values.** The `ib_async` library has no type stubs. When mapping ib_async values to typed models, use `cast(ExecSide, ex.side)` to assert the correct type. This keeps mypy happy without `# type: ignore`.
- **Use `@overload` for sentinel-default patterns.** When a function accepts an optional default via a sentinel (e.g. `_UNSET = object()`), use `@overload` to express the two call signatures instead of `# type: ignore` on the return.

## Pydantic Best Practices

- **Use `Field(default_factory=list)`** for mutable defaults (`list`, `dict`) **only when the field is genuinely optional.** Never use bare `[]` or `{}` as default values.
- **Do not add defaults to fields that are always populated.** A default makes the field optional in the generated JSON Schema and TypeScript types. If the construction code always provides the value, the field must be required (no default).
- **Use `ConfigDict(extra="forbid")`** on models that define an external contract (e.g. API request/response types). This produces `additionalProperties: false` in the JSON Schema, keeping generated TypeScript types strict.

## Error Handling (MANDATORY)

- **Every error must produce a clear, actionable message.** Include the relevant context (operation, input identifier, upstream status code, etc.).
- **API responses must never leak internal details.** Return structured error JSON with an appropriate HTTP status code and a human-readable `error` field. Never expose raw Python tracebacks, file paths, or internal class names.
- **Isolate failures.** The bridge has multiple concerns (connection, orders, trades). A failure in one should not take down the others.
- **Never silently swallow errors.** Every `except` block must either log the exception (`log.exception(...)`) or re-raise. A bare `except: pass` is never acceptable.
- **Use `log.exception()` for unexpected errors.** Reserve `log.error()` for known/expected failure conditions.
- **Distinguish recoverable from fatal errors.** Connection losses are recoverable (auto-reconnect). Missing config is fatal (`raise SystemExit(msg)`).
- **`SystemExit` must carry a descriptive message.** Never `raise SystemExit(1)`.
- **Env var parsing must fail fast, not fall back silently.** When parsing with `int()`, `float()`, etc., wrap in `try/except ValueError` and `raise SystemExit(...)`. Never silently fall back to a default on parse failure.
- **Validate at system boundaries, trust internally.** Validate all external inputs (API payloads, env vars, IB Gateway responses) at the point of entry. Once validated, internal code should not re-validate.
- **Never assume a default for financial enum fields.** When mapping IB Gateway values to constrained types, validate that the value is an exact match. For read-only fields with unbounded values (e.g. `TradeDetail.action`, `TradeDetail.orderType`), use `str` with a comment explaining why.
- **HTTP handlers must catch and map exceptions.** Route handlers distinguish `ValueError` (400) from `RuntimeError` (500) and return proper structured JSON responses.
- **Include context in error messages.** Bad: `"Order failed"`. Good: `"Contract qualification failed for AAPL: timeout after 20s"`.

## Concurrency Safety (MANDATORY)

- **Assume concurrency by default.** The bridge is async (aiohttp). Any handler can be interrupted at an `await`. When writing new code, always consider what happens if two requests arrive at the same time.
- **Always be wary of race conditions.** Before merging any code that touches shared state, ask: "Can two callers interleave here? What breaks if they do?"
- **The `IBClient` is shared across all handlers.** The `ib` connection object is stored on the `IBClient` singleton, which is shared via `aiohttp.web.AppKey` across all concurrent requests. Do not store request-specific state on the client.
- **Financial operations require extra scrutiny.** Any code path that places orders, moves money, or modifies account state must be reviewed for: race conditions, double-execution, partial failure, and idempotency.
- **Use `asyncio.get_running_loop()`, never `asyncio.get_event_loop()`.** `get_event_loop()` is deprecated since Python 3.10.
- **Reconnection is asynchronous.** `on_disconnect()` fires `asyncio.ensure_future(self._reconnect())` — handlers must check `client.is_connected` before performing operations and return 503 if disconnected.

## IB Gateway Connection

- **The HTTP server starts before the IB connection.** `main.py` binds the aiohttp server first, then calls `client.connect()`. This ensures `/health` is reachable (returning `connected: false`) while the Gateway is down or during reconnection. Handlers return 503 when `client.is_connected` is `False`.
- **The `IBClient` class manages the connection lifecycle.** It connects with exponential backoff (`INITIAL_RETRY_DELAY=10` to `MAX_RETRY_DELAY=300`), auto-reconnects on disconnect via the `disconnectedEvent` callback, and runs a 30-second watchdog loop.
- **Trading mode** is determined by `TRADING_MODE` env var (`paper` or `live`). Paper uses port 4004, live uses port 4003.
- **Client ID is hardcoded to 1.** Only one `IBClient` instance connects to the Gateway at a time.
- **Namespace delegation.** Orders and trades are separated into `OrdersNamespace` and `TradesNamespace`, each receiving the `ib_async.IB` instance. This keeps domain logic isolated from connection management.
- **Event wiring.** After connect, `subscribe_events()` registers `execDetailsEvent` and `commissionReportEvent` callbacks on the `IB` object. These map ib_async fills to `WsEnvelope` and broadcast via `EventHub`. Events survive reconnections because they're registered on the `IB` object, not the connection.

## Local Development

- **`.venv` is the project's virtual environment.** Created by `make setup` using Homebrew Python. All dev dependencies are installed there.
- **Auto-activation** is configured in `~/.zshrc` via a `chpwd` hook — the venv activates automatically when `cd`'ing into the project directory.
- **`make setup`** creates the `.venv` (if missing), installs all dependencies (`requirements-dev.txt` + service requirements), and writes a `.pth` file.
- **`ibkr-bridge.pth`** is created inside `.venv/lib/pythonX.Y/site-packages/` by `make setup`. It adds `services/bridge/` to `sys.path` so that `from bridge_models import ...`, `from client import ...`, and `from bridge_routes import ...` work everywhere (CLI, tests, scripts) without `sys.path` hacks or `PYTHONPATH`.
- **`.venv/` is gitignored** — never commit it.
- **`docker-compose.local.yml` adds bind mounts** that shadow the `COPY`'d files in the image with your local source tree (`:ro`). This means code changes are visible on container restart — no rebuild needed. `make local-up` builds the images once; after that, `make sync` (when `DEFAULT_CLI_BRIDGE_ENV=local`) just restarts containers.
- **`make sync` respects `DEFAULT_CLI_BRIDGE_ENV`.** When set to `local`, `make sync` restarts the local compose stack. When `prod` (default), it runs the full CLI sync to the droplet. Override per-command with `ENV=local` or `ENV=prod`.
- **`make logs` also respects `DEFAULT_CLI_BRIDGE_ENV`.** `make logs S=bridge` streams local container logs when local, droplet logs when prod.

## Dependency Management

- **Runtime deps (`services/bridge/requirements.txt`)** use exact pins (`==`). These are deployed to production containers — builds must be reproducible.
- **Dev deps (`requirements-dev.txt`)** use major-version constraints (`>=X,<X+1`). This allows minor/patch updates while preventing breaking changes.
- **When adding a new dependency**, always pin it immediately — never leave it unpinned. Use exact pin for runtime, major-version constraint for dev.

## Docker

- **Never use `env_file:` in service definitions.** Always declare each env var explicitly in the `environment:` block with `${VAR}` interpolation. This prevents `.env` leaking across compose override files.
- **`.dockerignore` uses an allowlist** (`*` to exclude everything, then `!services/bridge/**` to include the module). Tests, `__pycache__`, and the Dockerfile itself are re-excluded.
- The bridge Dockerfile uses directory COPYs (`COPY services/bridge/client/ ./client/`, `COPY services/bridge/bridge_routes/ ./bridge_routes/`) so new files are picked up automatically.
- **Never nest bind mounts in compose override files.** Docker will auto-create empty host directories to back nested mount points, which shadow real content on restart. Mount at separate paths outside `/app` instead.

## Architecture

Five Docker containers in a single Compose stack on a DigitalOcean droplet:

| Service              | Role                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------- |
| `ib-gateway`         | IB Gateway (Java) — TWS API on 4003/4004, VNC on 5900. Image: `ghcr.io/gnzsnz/ib-gateway`    |
| `bridge`             | Python REST API — connects to Gateway via ib_async, exposes `/ibkr/order` and `/ibkr/trades` |
| `novnc`              | Browser-based VNC client for 2FA and Gateway monitoring                                      |
| `caddy`              | Reverse proxy with automatic HTTPS (Let's Encrypt). Routes API and VNC traffic.              |
| `gateway-controller` | Alpine + Docker CLI — HTTP endpoints to start/check the Gateway container via Docker socket  |

All secrets are injected via `.env` → `environment` in `docker-compose.yml`.
Caddy reads `SITE_DOMAIN` and `VNC_DOMAIN` from env vars.

### Caddy Snippet Structure

The Caddyfile uses `import` directives to compose routing from snippet files:

```
infra/caddy/
  Caddyfile              # Shell: imports from sites/, domains/, and shared dirs
  docker-entrypoint.sh   # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH, then starts Caddy
  sites/
    ibkr-bridge.caddy    # SITE_DOMAIN route handlers (/ibkr/order, /ibkr/trades, /health)
  domains/
    ibkr-vnc.caddy       # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
```

Shared projects deploy snippets to `/opt/caddy-shared/` on the droplet (not into the host project's directory). The host Caddy mounts:

- `./infra/caddy/sites/` → `/etc/caddy/sites/` (host project's own routes)
- `./infra/caddy/domains/` → `/etc/caddy/domains/` (host project's domain blocks)
- `/opt/caddy-shared/sites/` → `/etc/caddy/shared-sites/` (shared projects' routes)
- `/opt/caddy-shared/domains/` → `/etc/caddy/shared-domains/` (shared projects' domains)

- **`sites/*.caddy`** contain `handle` blocks imported inside the `{$SITE_DOMAIN}` site definition. Routes must be prefixed with the project name (`/ibkr/*`) to avoid collisions.
- **`domains/*.caddy`** contain full site blocks for additional domains (e.g. `{$VNC_DOMAIN}`).
- This structure allows multiple projects to share a single Caddy instance on the same droplet.

## Deployment Modes

The deployment mode is controlled by `DEPLOY_MODE` in `.env` (required, validated before any deploy or sync).

### Standalone Mode (`DEPLOY_MODE=standalone`)

- Set `DO_API_TOKEN` in `.env`. `make deploy` runs Terraform to create a new droplet, firewall, and reserved IP, then the CLI rsyncs project files, pushes `.env`, and runs `docker compose up -d --build`.
- Terraform only creates infrastructure — cloud-init installs Docker and creates the project directory. The CLI handles all file transfer and service startup.
- After deploy, add `DROPLET_IP` from terraform output to `.env` for `make sync`.
- `DO_API_TOKEN` can be removed after first deploy for security.

### Shared Mode (`DEPLOY_MODE=shared`)

- Set `DROPLET_IP` and `SSH_KEY` in `.env` (no `DO_API_TOKEN` needed).
- `make deploy` rsyncs files, pushes `.env`, and starts services using `docker-compose.shared.yml` overlay.
- The shared overlay disables Caddy (the host project runs it) and connects all containers to the shared Docker network (`SHARED_NETWORK` env var, typically `relay-net`).
- **`SHARED_NETWORK` controls cross-project networking.** The base `docker-compose.yml` uses `name: ${SHARED_NETWORK:-}` for the default network. When unset, Docker Compose creates a project-scoped network (isolated). When set to the same value across projects (e.g. `relay-net`), all projects share a single network and can reach each other's containers by service name. The shared overlay (`docker-compose.shared.yml`) sets the network to `external: true`, which merges on top of the base definition.
- Caddy snippet files are deployed to the host project's Caddy to enable routing.
- `make sync` uses the shared compose overlay automatically.

## Droplet Sizing

- Droplet size is auto-selected based on `JAVA_HEAP_SIZE` (IB Gateway's Java heap). Higher heap = larger droplet.
- Override with `DROPLET_SIZE` in `.env` to use a specific slug regardless of heap size.
- `cli/__init__.py` `_droplet_size()` implements the sizing logic.
- `cli/core/resume.py` uses `cfg.droplet_size()` which delegates to the same function.

| Heap (MB) | Droplet        | RAM   |
| --------- | -------------- | ----- |
| ≤ 1024    | `s-1vcpu-2gb`  | 2 GB  |
| ≤ 3072    | `s-2vcpu-4gb`  | 4 GB  |
| ≤ 6144    | `s-4vcpu-8gb`  | 8 GB  |
| > 6144    | `s-8vcpu-16gb` | 16 GB |

## Auth Pattern

- API endpoints under `/ibkr/*` require `Authorization: Bearer <API_TOKEN>` (HMAC-safe comparison via `hmac.compare_digest`).
- **All authenticated routes must use the `AUTH_PREFIX` constant** (from `bridge_routes.constants`) when registering with the router. The auth middleware uses the same constant to decide which requests require a token — hardcoding the path in either place causes them to drift out of sync.
- `/health` is unauthenticated — used for monitoring and load balancer checks.

## WebSocket Event Streaming

- **`GET /ibkr/ws/events`** upgrades to WebSocket and streams real-time execution events to subscribers.
- **Auth** uses the same `auth_middleware` — the path starts with `/ibkr/`, so `Authorization: Bearer <API_TOKEN>` is required in the upgrade request headers.
- **`EventHub`** (`client/event_hub.py`) is the pub/sub core:
  - Global ring buffer (`collections.deque`) stores last `WS_BUFFER_SIZE` events (default 500).
  - Each subscriber gets an `asyncio.Queue` for delivery.
  - `broadcast()` assigns a monotonic `seq` number, appends to buffer, and pushes to all subscriber queues.
  - `replay(from_seq)` returns buffered events with `seq > from_seq`.
- **`IBClient.subscribe_events()`** wires `ib.execDetailsEvent` and `ib.commissionReportEvent` callbacks to the hub. Called once after initial connection. Events survive reconnections (registered on the `IB` object, not the connection).
- **Message format**: `WsEnvelope` (type, seq, timestamp, fill). Event types: `execDetailsEvent`, `commissionReportEvent`, `connected`, `disconnected`.
- **Zombie detection**: `WebSocketResponse(heartbeat=WS_HEARTBEAT_INTERVAL)` sends pings; aiohttp auto-closes unresponsive connections. Cleanup runs in `try/finally` to unsubscribe.
- **Max subscribers**: `WS_MAX_SUBSCRIBERS` (default 10). Exceeding returns WS close code 4029.
- **Reconnect replay**: client passes `?last_seq=N` to receive missed events from the ring buffer.
- **No new port needed**: WebSocket runs on the same aiohttp server (port 5000). Caddy proxies it transparently (no special upgrade config needed, unlike Nginx).

## E2E Testing

- **E2E tests run against a local Docker stack** defined by `docker-compose.test.yml` (ib-gateway + bridge, no Caddy/noVNC/controller).
- **Paper account credentials are required** — real orders are placed in paper mode.
- **Credentials live in `.env.test`** (gitignored). Template: `.env.test.example`.
- **`make e2e`** starts the stack, runs pytest, then tears down. Always cleans up, even on test failure.
- **`make e2e-up` / `make e2e-down`** for manual stack management during debugging.
- **`make e2e-up` waits up to 240 seconds** for the IB Gateway to connect. It detects session conflicts ("Existing session detected") and Gateway exits, failing fast with actionable error messages.
- **`make e2e-run`** restarts the `bridge` container (to pick up code changes from volume mounts), then runs the E2E tests. Safe to call repeatedly during development.
- **Test bridge runs on `localhost:15010`** with hardcoded token `test-token`.

## Test File Convention

- **Unit tests are colocated** next to the source file they test: `orders.py` → `test_orders.py`, `middlewares.py` → `test_middlewares.py`.
- **E2E tests live in `tests/e2e/`** within each service, since they test multiple components together.
- **`make test`** runs all unit tests. **`make e2e-run`** runs all E2E tests (requires Docker stack). **`make lint`** runs ruff. All must pass before deploying.
- **Always scope `unittest.mock.patch`.** Never call `patch.start()` at module level without a corresponding `patch.stop()` — the patched value leaks into every test module that runs afterward. Use one of these patterns instead:
  - **`setUpModule()` / `tearDownModule()`** — for module-wide patches.
  - **`self.addCleanup(patcher.stop)`** in `setUp()` — for class-scoped patches.
  - **`with patch(...):`** inside the test — for single-test patches.
  - **`@patch(...)`** decorator — for single-test or single-class patches.
  - Never use bare `_patcher.start()` without registering a `.stop()`.
- **Use `setUpModule()` / `tearDownModule()` for env var overrides.** When tests need specific `os.environ` values, save originals in `setUpModule()` and restore in `tearDownModule()`. The pattern:

  ```python
  _ORIG_ENV: dict[str, str | None] = {}
  _TEST_ENV = {"MY_VAR": "test-value"}

  def setUpModule() -> None:
      for key, val in _TEST_ENV.items():
          _ORIG_ENV[key] = os.environ.get(key)
          os.environ[key] = val

  def tearDownModule() -> None:
      for key, orig in _ORIG_ENV.items():
          if orig is None:
              os.environ.pop(key, None)
          else:
              os.environ[key] = orig
  ```

- **Avoid reading env vars at module level in production code.** Defer env reads to a getter function so tests can set env vars normally in `setUpModule()` and get fresh reads on each call.
- **No cross-test dependencies.** Every test must be self-contained.
- **E2E conftest fixtures must use `yield` with a context manager.** Use `with httpx.Client(...) as client: yield client`. Scope to `session`. Every E2E `conftest.py` must include a `_preflight_check` fixture (`scope="session"`, `autouse=True`) that hits `/health` and calls `pytest.exit()` if the stack is unreachable.

## Bridge Structure

The `services/bridge/` service is the only service in this project:

```
services/bridge/
  main.py                  # Entrypoint (IB connection + HTTP server startup)
  bridge_models.py         # Pydantic models (all request/response types + Literal aliases)
  client/                  # IB Gateway client (package)
    __init__.py            # IBClient class (connection, reconnection, watchdog, event wiring)
    event_hub.py           # EventHub (pub/sub broadcast + ring buffer for WS replay)
    orders.py              # OrdersNamespace (place orders)
    trades.py              # TradesNamespace (list trades + fills)
    test_event_hub.py      # Tests for EventHub
    test_orders.py         # Tests for orders
    test_trades.py         # Tests for trades
  bridge_routes/           # HTTP API
    __init__.py            # Route orchestrator (create_routes)
    constants.py           # Shared constants (AUTH_PREFIX, client_key, hub_key)
    health.py              # GET /health handler
    middlewares.py         # Auth middleware (Bearer token, HMAC-safe)
    order_place.py         # POST /ibkr/order handler
    trades_list.py         # GET /ibkr/trades handler
    ws_events.py           # GET /ibkr/ws/events WebSocket handler
    test_middlewares.py    # Tests for auth middleware
    test_order_place.py    # Tests for order handler
    test_trades_list.py    # Tests for trades handler
    test_ws_events.py      # Tests for WebSocket handler
  tests/e2e/               # E2E tests (require IB Gateway)
    conftest.py            # httpx fixtures (api + anon_api, preflight check)
    test_smoke.py          # Health + auth smoke tests
  Dockerfile
  requirements.txt         # ib_async, aiohttp, httpx, pydantic
```

- **`services/bridge/client/`** contains IB Gateway client logic: connection management, order placement, trade listing, and event broadcasting. `IBClient` is the main class; `OrdersNamespace` and `TradesNamespace` handle domain logic; `EventHub` manages pub/sub for WebSocket subscribers.
- **`services/bridge/bridge_routes/`** contains the HTTP API: route registration, auth middleware, request handlers, and the WebSocket event stream endpoint.
- **`services/bridge/bridge_models.py`** defines all public Pydantic models and Literal type aliases. Every type in this file is exported to consumers via `make types` (TypeScript + Python packages). Do not add internal helpers here.

## Models (Two Locations)

This project has **two model locations** — a shared source of truth (currently empty) and one service-specific file:

| File                          | Domain                 | Contains                                                                            |
| ----------------------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| `services/shared/__init__.py` | Shared (outbound)      | Reserved for cross-project types (future `IbkrBridge` TS namespace). Currently empty |
| `services/bridge/bridge_models.py` | Bridge HTTP + WS (outbound) | All HTTP API models, WS event models, and Literal type aliases (`IbkrBridgeHttp` TS namespace) |

- **`services/shared/__init__.py`** is reserved for shared/common types that multiple consumers depend on (the `IbkrBridge` primary namespace). When types are added here, they get their own `types/typescript/shared/` directory and `SCHEMA_MODELS` entry in `schema_gen.py`.
- **`services/bridge/bridge_models.py`** is the single source of truth for all bridge-specific types (HTTP API + WS events). Every type in this file is exported to consumers via `make types` under the `IbkrBridgeHttp` namespace.
- All external-contract models use `ConfigDict(extra="forbid")` for strict validation.

| Model                    | Direction | Description                                              |
| ------------------------ | --------- | -------------------------------------------------------- |
| `PlaceOrderPayload`      | Inbound   | `POST /ibkr/order` request body (contract + order)       |
| `ContractPayload`        | Inbound   | Contract fields (symbol, secType, exchange, currency)    |
| `OrderPayload`           | Inbound   | Order fields (action, qty, type, price, tif)             |
| `PlaceOrderResponse`     | Outbound  | Order placement result (status, orderId, etc.)           |
| `HealthResponse`         | Outbound  | `GET /health` response                                   |
| `ListTradesResponse`     | Outbound  | `GET /ibkr/trades` response (array of TradeDetail)       |
| `TradeDetail`            | Outbound  | Order + status + fills                                   |
| `FillDetail`             | Outbound  | Single execution fill within a trade                     |
| `WsEnvelope`             | Outbound  | WebSocket message wrapper (type, seq, timestamp, fill)   |
| `WsFill`                 | Outbound  | Fill payload (contract + execution + commissionReport)   |
| `WsContract`             | Outbound  | Mirrors `ib_async.Contract` (ib_async 2.1.0)             |
| `WsExecution`            | Outbound  | Mirrors `ib_async.Execution` (ib_async 2.1.0)            |
| `WsCommissionReport`     | Outbound  | Mirrors `ib_async.CommissionReport` (ib_async 2.1.0)     |
| `WsComboLeg`             | Outbound  | Mirrors `ib_async.ComboLeg` (ib_async 2.1.0)             |
| `WsDeltaNeutralContract` | Outbound  | Mirrors `ib_async.DeltaNeutralContract` (ib_async 2.1.0) |

Type aliases: `Action`, `ExecSide`, `OrderType`, `SecType`, `TimeInForce`, `WsEventType` — all `Literal` types used across models.

- `TradeDetail.action` and `TradeDetail.orderType` are `str` (not `Action`/`OrderType`) because IB Gateway returns values beyond our constrained Literals for existing orders (e.g. `STP`, `TRAIL`).
- `WsEnvelope.type` uses `WsEventType` as the exported WebSocket event discriminator. Do not document or rely on a separate public `WsStatusType` alias unless it is also emitted by the schema/type generation pipeline.
- **WS event models mirror `ib_async` 2.1.0 exactly** — same field names, same nesting (`WsFill.contract`, `WsFill.execution`, `WsFill.commissionReport`). When bumping ib_async, update these models to match.

## Gateway Controller

The `infra/gateway-controller/` container provides HTTP endpoints to start/check the IB Gateway container without SSH:

- **`POST /cgi-bin/start-gateway`** — starts the `ib-gateway` container via Docker socket.
- **`GET /cgi-bin/gateway-status`** — returns the `ib-gateway` container state.
- **Container discovery uses Compose labels** (`com.docker.compose.service=ib-gateway` + `com.docker.compose.project`), not hardcoded container names. This is robust across project name changes and Compose naming conventions.
- These are served via busybox httpd as CGI scripts. The container mounts the Docker socket (`/var/run/docker.sock`). Caddy rewrites `/gateway/*` to `/cgi-bin/*` before proxying.
- Exposed at `https://{VNC_DOMAIN}/gateway/*` via Caddy reverse proxy.
- **The entire VNC domain is protected by HTTP Basic Auth.** Caddy's `basic_auth` directive uses a bcrypt hash of `VNC_SERVER_PASSWORD`, generated at container startup by `infra/caddy/docker-entrypoint.sh`. The username defaults to `admin` and can be overridden via `VNC_BASIC_AUTH_USER` env var.

## TypeScript Types

### Namespace Convention (cross-project standard)

All projects export TypeScript types using a two-tier namespace pattern:

- **`types/typescript/`** (or `types/shared/` in relays) → exported as the **project's primary namespace** (e.g. `IbkrBridge`). Reserved for shared/common types that multiple consumers depend on. Currently empty — no shared types yet.
- **`types/typescript/<module>/`** → exported as **`<ProjectName><ModuleName>`** (e.g. `IbkrBridgeHttp`). Contains module-specific types generated from that module's `SCHEMA_MODELS`.

The barrel `types/typescript/index.d.ts` currently exports only the HTTP namespace:

```ts
import * as IbkrBridgeHttp from "./http";
export { IbkrBridgeHttp };
```

When shared types are added in the future, the barrel will grow:

```ts
import * as IbkrBridge from "./shared";
import * as IbkrBridgeHttp from "./http";
export { IbkrBridge, IbkrBridgeHttp };
```

**`IbkrBridge` is reserved for the primary/shared namespace — do not use it for module-specific types.**

### IBKR Bridge Types

- Types are published as `@tradegist/ibkr-bridge-types` (npm package in `types/typescript/`, not yet published).
- **One namespace**: `IbkrBridgeHttp` (HTTP API + WS event types).
- **`make types`** regenerates from Pydantic models:
  - `services/bridge/bridge_models.py` → `types/typescript/http/types.d.ts` (TypeScript)
  - `services/bridge/bridge_models.py` → `types/python/ibkr_bridge_types/models.py` (Python, via `gen_python_types.py`)
- **Structure:**
  ```
  types/
    typescript/
      index.d.ts                 # Barrel: exports IbkrBridgeHttp namespace
      package.json               # @tradegist/ibkr-bridge-types
      http/
        index.d.ts               # Re-exports all types
        types.d.ts               # Generated from bridge_models.py (SCHEMA_MODELS)
        types.schema.json         # Intermediate JSON Schema
    python/
      ...
  ```
- **Usage:** `import { IbkrBridgeHttp } from "@tradegist/ibkr-bridge-types"`
- `schema_gen.py` owns the `SCHEMA_MODELS` dict that lists which top-level models go into the JSON Schema. **To add a new model to the TypeScript types, add it to `SCHEMA_MODELS` in `schema_gen.py` and update `types/typescript/http/index.d.ts` re-exports.** The Python types package copies the entire `bridge_models.py` file automatically.

## Python Types Package

- Types are available as `ibkr-bridge-types` (PyPI package in `types/python/`, not yet published).
- **Standalone Pydantic models** — no dependency on `ib_async` or the bridge service.
- Exports the **same public types** as the TypeScript package: HTTP API models, WS event models, and Literal type aliases.
- **Structure:**
  ```
  types/python/
    pyproject.toml              # ibkr-bridge-types, deps: pydantic
    ibkr_bridge_types/
      __init__.py               # Re-exports all public types
      models.py                 # All models + type aliases (generated from bridge_models.py)
  ```
- **Usage:** `from ibkr_bridge_types import PlaceOrderPayload, WsEnvelope, Action`
- **Auto-generated** — `models.py` is extracted from `bridge_models.py` by `gen_python_types.py`. Run `make types` to regenerate. Do not edit `models.py` manually.
- **Covered by `make lint` and `make typecheck`** — `types/python/ibkr_bridge_types/` is included in both targets. Generated code must pass ruff and mypy like any other Python module.
- When bumping `ib_async`, update `bridge_models.py` and run `make types` to regenerate both TS and Python types.

## Code Style

- Python: `logging` module, f-strings, `aiohttp` for the async HTTP server, `ib_async` for IB Gateway communication.
- CLI scripts: Python (`cli/` package), invoked via `python3 -m cli <command>` or `make`. Uses only stdlib (`subprocess`, `urllib.request`, `json`, `os`). No third-party dependencies. Uses lazy dispatch (`importlib.import_module`).
- Terraform: all secrets marked `sensitive = true` in `variables.tf`.

## Build & Deploy

All commands available via `make` or `python3 -m cli <command>`:

```bash
make deploy    # Standalone: Terraform | Shared: rsync + compose
make sync      # Push .env to droplet + restart services
make sync LOCAL_FILES=1  # rsync files + rebuild + restart (full code deploy)
make destroy   # Terraform destroy
make pause     # Snapshot + delete droplet (save costs)
make resume    # Restore from snapshot
make order Q=10 SYM=AAPL T=MKT  # Place an order
make e2e       # Run E2E tests (starts/stops stack)
make lint      # Run ruff linter (FIX=1 to auto-fix)
```

Direct CLI:

```bash
python3 -m cli deploy
python3 -m cli sync --local-files
python3 -m cli order 10 AAPL MKT
python3 -m cli order -5 TSLA LMT 250.00
```

## Deployment Model (MANDATORY)

- **`make sync LOCAL_FILES=1` uses rsync** to transfer files from the local working tree to `/opt/ibkr-bridge/` on the droplet. It does NOT use git on the droplet.
- **Guards:** Must be on `main` branch with a clean working tree (no uncommitted changes).
- **`--delete` flag:** rsync removes files on the droplet that no longer exist locally.
- **Invariant: the project directory (`/opt/ibkr-bridge/`) contains only source files.** No service, script, or container may write files into the project directory. All runtime-generated data (databases, caches, certificates) MUST use Docker named volumes (e.g. `caddy-data:/data`).
- **`.deployed-sha`** is the only server-side file inside the project directory. It records the deployed commit SHA.
- **rsync exclusions:** `.git/`, `.env`, `.env.test`, `.deployed-sha`, and everything in `.gitignore`.

## File Structure

```
.env.example              # Template — copy to .env and fill in real values
docker-compose.yml        # All services (ib-gateway, bridge, novnc, caddy, gateway-controller)
docker-compose.shared.yml # Shared-mode overlay (disables Caddy, uses SHARED_NETWORK)
docker-compose.local.yml  # Local dev override (direct port access, no TLS)
docker-compose.test.yml   # Test stack override (paper mode, no Caddy/noVNC)
cli/                      # Python CLI (operator scripts)
  __init__.py             # Project-specific config (CoreConfig, helpers, bridge_api)
  __main__.py             # Entry point (lazy dispatch via importlib)
  order.py                # Place an order via CLI
  core/                   # Project-agnostic (reusable across projects)
    __init__.py           # CoreConfig dataclass, generic helpers (env, SSH, DO API, Terraform)
    deploy.py             # Standalone (Terraform) or shared (rsync + compose)
    destroy.py            # Terraform destroy
    pause.py              # Snapshot + delete droplet
    resume.py             # Restore from snapshot
    sync.py               # rsync files + pre-deploy checks + restart
services/
  shared/               # Shared models (future IbkrBridge TS namespace, currently empty)
    __init__.py
  bridge/                 # REST API service (see Bridge Structure above)
    main.py               # Entrypoint (IB connection + HTTP server)
    bridge_models.py      # Pydantic models (all public types — exported to TS + Python packages)
    client/               # IB Gateway client (connection, orders, trades)
    bridge_routes/        # HTTP API (routes, middleware, handlers)
    constants.py        # Shared constants (AUTH_PREFIX, client_key)
    tests/e2e/            # E2E tests (require Docker stack)
    Dockerfile
    requirements.txt
infra/
  caddy/
    Caddyfile             # Reverse proxy config (SITE_DOMAIN + VNC_DOMAIN)
    docker-entrypoint.sh  # Hashes VNC_SERVER_PASSWORD → VNC_BASIC_AUTH_HASH, then starts Caddy
    sites/
      ibkr-bridge.caddy   # SITE_DOMAIN API routes (/ibkr/order, /ibkr/trades, /ibkr/ws/events, /health)
    domains/
      ibkr-vnc.caddy      # VNC_DOMAIN routes (noVNC + gateway-controller, basic auth)
  gateway-controller/     # CGI container for Gateway lifecycle control
    Dockerfile
    start-gateway.sh
    gateway-status.sh
  novnc/
    index.html            # Custom noVNC landing page
terraform/
  main.tf                 # Droplet, firewall, reserved IP, SSH key
  variables.tf            # Terraform variables (infrastructure only)
  outputs.tf              # Droplet IP, VNC URL, Site URL, SSH key
  cloud-init.sh           # Docker install + project directory
schema_gen.py             # JSON Schema generator (Pydantic → TS types)
gen_python_types.py       # Python types generator (bridge_models.py → models.py)
types/
  typescript/              # @tradegist/ibkr-bridge-types npm package
    index.d.ts            # Barrel: exports IbkrBridgeHttp namespace
    package.json
    http/                 # IbkrBridgeHttp namespace
      index.d.ts
      types.d.ts          # Generated from bridge_models.py SCHEMA_MODELS
  python/                 # ibkr-bridge-types PyPI package
    pyproject.toml
    ibkr_bridge_types/
      __init__.py         # Re-exports all public types
      models.py           # All models + type aliases (generated from bridge_models.py)
```
