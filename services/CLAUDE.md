# `services/` — Shared rules across all services

Test conventions and the two-location model layout. Service-specific rules live in each subdirectory's `CLAUDE.md`.

## Models (Two Locations)

| File | Domain | Status |
| --- | --- | --- |
| `services/shared/__init__.py` | Cross-project shared types (future `IbkrBridge` TS namespace) | Reserved; currently no models defined |
| `services/bridge/bridge_models.py` | All bridge HTTP + WS types (`IbkrBridgeHttp` TS namespace) | Single source of truth |

- **`services/bridge/bridge_models.py`** is the single source of truth for all public bridge types (HTTP API + WS events + Literal aliases). Every type listed in `schema_gen.py:SCHEMA_MODELS` is regenerated to TypeScript + Python type packages via `make types`. **Do not add internal/private helpers here** — they leak into the generated public types.
- **`services/shared/__init__.py`** is reserved for future shared/common types. When shared models are added, register them under a new `"shared"` entry in `schema_gen.py:SCHEMA_MODELS` and update the hand-maintained `types/typescript/index.d.ts` barrel.
- All external-contract models use `ConfigDict(extra="forbid")` for strict validation.
- After modifying `bridge_models.py`, run `make types` to regenerate the TypeScript + Python type packages.

## Test File Convention

- **Unit tests are colocated** next to the source file: `orders.py` → `test_orders.py`, `middlewares.py` → `test_middlewares.py`.
- **E2E tests live in `tests/e2e/`** within each service.
- **`make test`** runs all unit tests. **`make e2e-run`** runs E2E tests (requires Docker stack). **`make lint`** runs ruff. All must pass before deploying.
- **Always scope `unittest.mock.patch`.** Never call `patch.start()` at module level without a corresponding `patch.stop()` — the patched value leaks into every later test module. Use:
  - **`setUpModule()` / `tearDownModule()`** for module-wide patches.
  - **`self.addCleanup(patcher.stop)`** in `setUp()` for class-scoped.
  - **`with patch(...):`** inside a test for single-test.
  - **`@patch(...)`** decorator for single-test or single-class.
- **Use `setUpModule()` / `tearDownModule()` for env var overrides.** Save originals, restore on tear-down. Never mutate `os.environ` at module level without cleanup. Pattern:

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

  Both are called automatically by pytest/unittest. Prefer this over `mock.patch.dict(os.environ, ...)` for module-wide overrides. For single-test changes, use `with mock.patch.dict(os.environ, ...):`.

- **Avoid reading env vars at module level in production code.** Module-level reads bake values at import time, forcing tests to set vars before imports. Defer to a getter function so `setUpModule()` works normally.
- **No cross-test dependencies.** Every test must be self-contained. Pytest does not guarantee execution order.
- **Tests patching `loop.call_later` must be async.** Use `asyncio.get_running_loop()` (not `get_event_loop()`) and run as `async def` methods on `IsolatedAsyncioTestCase` — `get_event_loop()` in a sync test silently creates a fresh loop that nobody runs, masking real bugs.
- **E2E conftest fixtures must use `yield` with a context manager.** Never `return httpx.Client(...)` — leaks sockets. Use `with httpx.Client(...) as client: yield client`. Scope to `session`. Every E2E `conftest.py` must include a `_preflight_check` fixture (`scope="session"`, `autouse=True`) that hits `/health` and calls `pytest.exit()` if the stack is unreachable.

## E2E Testing

- E2E tests run against a local Docker stack defined by `docker-compose.test.yml` (ib-gateway + bridge, no Caddy/noVNC/controller).
- **Paper account credentials required** — real orders are placed in paper mode.
- Credentials live in `.env.test` (gitignored). Template: `env_examples/env.test`.
- **`make e2e-up` waits up to 240 seconds** for the IB Gateway to connect. It detects session conflicts ("Existing session detected") and Gateway exits, failing fast with actionable error messages.
- **`make e2e-run`** restarts the `bridge` container (to pick up code changes from volume mounts), then runs the E2E tests. Safe to call repeatedly during development.
- **Test bridge runs on `localhost:15010`** with hardcoded token `test-token`.
- `make e2e` starts the stack, runs pytest, tears down. Always cleans up.
- `make e2e-up` / `make e2e-down` for manual stack management during debugging.
