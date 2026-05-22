# IBKR Bridge — Project Guidelines

A Python/aiohttp bridge between IB Gateway and consumer services (`relayport` and others). Exposes a small REST + WebSocket API for placing orders, listing trades, and streaming fills.

This file holds **cross-cutting rules** that apply everywhere. Per-directory rules live in nested `CLAUDE.md` files (loaded on demand when Claude touches files in that subtree). Architectural prose lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Where to find more guidance

| When you are working in… | Read… |
|---|---|
| `cli/` | [cli/CLAUDE.md](cli/CLAUDE.md) — deploy modes, env files, rsync invariant, Makefile rules |
| `services/` (anywhere) | [services/CLAUDE.md](services/CLAUDE.md) — test conventions, model layout, E2E |
| `services/bridge/` | [services/bridge/CLAUDE.md](services/bridge/CLAUDE.md) — IB Gateway connection, auth, WS streaming, reconcile |
| `services/shared/` | [services/shared/CLAUDE.md](services/shared/CLAUDE.md) — reserved-but-empty shared namespace |
| `infra/` | [infra/CLAUDE.md](infra/CLAUDE.md) — Caddy snippets, `route_prefixes` |
| `infra/gateway-controller/` | [infra/gateway-controller/CLAUDE.md](infra/gateway-controller/CLAUDE.md) — controller endpoints + monitor-gateway |
| `types/` | [types/CLAUDE.md](types/CLAUDE.md) — TS + Python types regeneration |

**Playbooks** (rare procedures) live as skills in [.claude/skills/](.claude/skills/):
- `export-new-model-to-types` — register a name in `schema_gen.py:SCHEMA_MODELS` and regenerate
- `bump-ib-async-version` — keep `bridge_models.py` WS shapes in sync with `ib_async`

## Sibling Project: relayport

This project (`ibkr_bridge`) and its sibling project `relayport` share the same CLI deploy/destroy/sync infrastructure pattern. **Any change to `cli/core/deploy.py`, `cli/core/destroy.py`, or `cli/core/sync.py` in this project must be mirrored in the sibling project, and vice versa.** This includes: Terraform state management, reserved IP handling, rsync exclusions, env file push logic, and compose startup commands. When you modify CLI core logic here, explicitly remind the user to apply the equivalent change to `relayport`, and offer to do it in the same session.

## Code Quality (MANDATORY)

- **Always apply best practices by default.** Use idiomatic Python naming, file organization, and patterns. When a clearly better approach exists, use it directly and explain why — don't ask permission.
- **NEVER use deprecated APIs.** Examples: `asyncio.get_event_loop()` → `asyncio.get_running_loop()`; `datetime.utcnow()` → `datetime.now(UTC)`; Pydantic v1 `parse_obj` / `dict()` → v2 `model_validate` / `model_dump`. Scan docs for "deprecated" before relying on anything new. A deprecation warning in CR is a regression — fix the call, don't suppress.
- **No unused imports.** After any edit, verify every `import` is used. Remove what isn't.
- **No `__all__`.** All imports are explicit (`from module import X`); star-imports are never used. `make lint` has a hard fail that greps for `__all__` and aborts.
- **No `assert` for runtime guards.** `assert` is stripped under `python -O`. Use `if … raise RuntimeError(...)` (or `die()`) for invariants that must hold at runtime.
- **Run `make lint` after every code change.** Ruff enforces unused imports, import ordering, unused variables, bugbear pitfalls, modern idioms. `make lint FIX=1` auto-fixes safe issues.
- **Centralise env var reads into typed getter functions.** Each env var is read in exactly one place — a getter in the module that owns it (e.g. `get_ib_host()` in `client/__init__.py`). Getters apply `.strip()` and type conversion. Never call `os.environ.get()` inline outside a getter.
- **Getters must validate and fail fast.** Every getter must validate and `raise SystemExit("<descriptive message>")` on bad input. Wrap `int()`/`float()` in `try/except ValueError`. Check emptiness on required strings. Callers should never have to validate a getter's return value.
- **Prefer pure functions over side-effect functions.** Compute and return values; let the caller decide. If a side-effect function is truly unavoidable, add an inline comment at every call site explaining what is mutated and why.
- **Never bulk-set `os.environ` with empty-string fallbacks.** `os.environ[key] = env(name, "")` silently overrides downstream defaults (Terraform `variable` defaults, library config) with empty strings, breaking `tonumber()`, validation blocks, and non-string parsing. Only export when source is present and non-empty; `os.environ.pop(key, None)` otherwise.
- **Verify Markdown table integrity after every edit.** Count column dividers on changed row(s) AND the header/separator rows — all must match. Known failure modes: (1) bare `|` inside a cell splits the row — escape as `\|` or rewrite; (2) extra `| ----- |` in separator. Sanity check: `awk -F'\|' 'NR>=START && NR<=END { print NR": "NF" cells" }' file.md`.
- **Update README.md when changing public interfaces.** CLI commands, Makefile targets, API endpoints, env vars.
- **Register new modules in `pyproject.toml`.** When adding a new Python package or module under `services/` or `types/python/`, immediately add it to `pyproject.toml` (`testpaths`, `tool.ruff.src`, `known-first-party`) and the Makefile `lint:`/`typecheck:` targets.

## Security Rules (MANDATORY)

- **No hardcoded credentials.** Passwords, API tokens, secrets, keys must come from env vars (`.env`, `TF_VAR_*`). Never write real values in source files.
- **No hardcoded IPs.** Use `DROPLET_IP` from `.env.droplet`. In docs use `1.2.3.4` as placeholder.
- **No hardcoded domains.** Use `example.com` variants in docs and code (`trade.example.com`, `vnc.example.com`); actual domains loaded at runtime via `SITE_DOMAIN` / `VNC_DOMAIN`.
- **No email addresses or personal info.** Never write real names, emails, or IBKR account IDs in committed files.
- **No developer-machine paths.** Never write absolute paths like `/Users/john/…` or `C:\Users\john\…` in committed files. Reference sibling projects by name only.
- **No logging of secrets or sensitive operational data.** Never `log.info()` tokens, passwords, API keys, account IDs, IPs, or domains. Log actions and outcomes, not credential values. Prefer counts, symbols, statuses over full objects.
- **`.env`, `.env.droplet`, `.env.test`, `*.tfvars` are gitignored.** Never commit them. Use `env_examples/` templates with placeholder values.
- **Terraform state is gitignored** — `terraform.tfstate` contains SSH keys and IPs.
- **Auth middleware must reject empty `API_TOKEN`.** `hmac.compare_digest("", "")` returns `True`, so an empty `API_TOKEN` silently disables auth. The middleware checks `if not api_token: return HTTP 500` **before** `compare_digest`. `API_TOKEN` is in `required_env` for standalone deploy, where the CLI blocks deployment if missing; sync/shared deploy rely on correct `.env` contents rather than explicit `required_env` validation.

## Type Safety (MANDATORY)

- **Python >= 3.11.** Uses `X | None` union syntax natively (no `from __future__ import annotations`). Docker uses `python:3.11-slim`.
- **Run `make typecheck`, `make test`, and `make lint` after every code change.** Non-negotiable before deploying. mypy + ruff + pytest must all pass. `make typecheck` also runs `tsc --noEmit` on `types/typescript/`.
- **Run E2E tests after modifying any E2E test OR infrastructure file** (`docker-compose*.yml`, `Dockerfile`, `Caddyfile`, anything under `infra/`). E2E requires the Docker stack with IB Gateway. Workflow:
  1. `make e2e-up` — start the stack (idempotent, waits up to 240s for Gateway connection).
  2. `make e2e-run` — run tests.
  3. Fix code → `make e2e-run` → repeat. Volume mounts keep code in sync — no rebuild.
  4. `make e2e-down` — tear down **only after all tests pass**.
- **Every Python file must be covered by `make typecheck`.** New module → add to the mypy invocation in the Makefile.
- After modifying any model in `services/bridge/bridge_models.py`, run `make types` to regenerate TypeScript + Python type packages.
- **Always verify type safety by breaking it first.** After any refactor that touches types, deliberately introduce a type error, run `make typecheck`, confirm it **fails**. Then revert. Never assume mypy catches something — prove it.
- **Avoid `dict[str, Any]` round-trips.** Never `model_dump()` → `dict` → `Model(**data)`. Use explicit kwargs or `model_copy(update=...)`.
- **Prefer strict `Literal` types over bare `str` on Pydantic models.** Financial code demands precision. Use existing `Literal` aliases (`Action`, `OrderType`, `SecType`, `TimeInForce`, `ExecSide`) when the value set is known. Fall back to `str` only when IB Gateway genuinely returns unbounded values — document why inline (see `TradeDetail.action` and `TradeDetail.orderType`).
- **No `# type: ignore` without justification.** Fix the root cause. If unavoidable (untyped `ib_async` attributes): `# type: ignore[attr-defined] # ib_async.Foo has no stubs`. Bare `# type: ignore` is never acceptable.
- **Use `cast()` instead of `# type: ignore[arg-type]`.** When passing a mock or compatible object where mypy expects a concrete type (e.g. `IBClient`), `cast(IBClient, mock)`. `cast()` preserves downstream type-checking; `# type: ignore` silently disables it.
- **Use `cast()` for `ib_async` values.** The library has no type stubs. When mapping `ib_async` values to typed models, `cast(ExecSide, ex.side)` asserts the correct type without `# type: ignore`.
- **Use `@overload` for sentinel-default patterns.** Express the two signatures via `@overload` instead of `# type: ignore` on the return.

## Pydantic Best Practices

- **`Field(default_factory=list)`** for mutable defaults — only when genuinely optional. Never bare `[]` or `{}`.
- **Do not add defaults to fields that are always populated.** A default makes the field optional in the generated JSON Schema / TS. Defaults only for fields that are legitimately absent.
- **`ConfigDict(extra="forbid")`** on external-contract models (API request/response, WS envelopes). Produces `additionalProperties: false` in JSON Schema, keeping generated TS strict.

## Error Handling (MANDATORY)

- **Every error must produce a clear, actionable message.** Include context: operation, input identifier, upstream status.
- **API responses must never leak internal details.** Return structured error JSON with appropriate HTTP status. Never expose tracebacks, paths, or class names to callers.
- **Isolate failures.** The bridge has multiple concerns (connection, orders, trades, WS broadcast). A failure in one must not take down the others.
- **Never silently swallow errors.** Every `except` must `log.exception(...)` or re-raise. Bare `except: pass` is never acceptable.
- **`log.exception()` for unexpected errors.** Reserve `log.error()` for known/expected failures where a traceback is noise.
- **Distinguish recoverable from fatal.** Connection losses are recoverable (auto-reconnect). Missing config → fail fast with `raise SystemExit(msg)`.
- **`SystemExit` must carry a descriptive message.** Never `raise SystemExit(1)`.
- **Env var parsing must fail fast, not fall back silently.** Wrap `int()`/`float()` in `try/except ValueError: raise SystemExit(...)`. Fall back only on _missing_ vars, never on _invalid_ values.
- **Validate at system boundaries, trust internally.** Validate at the entry point (API payloads, env vars, IB Gateway responses). Once validated, internal code does not re-validate.
- **Never assume a default for financial enum fields.** When mapping IB Gateway values to constrained types, validate exactly. For read-only fields with genuinely unbounded values (e.g. `TradeDetail.action`, `TradeDetail.orderType`), use `str` with an inline comment explaining why.
- **HTTP handlers must catch and map exceptions.** Distinguish `ValueError` (400) from `RuntimeError` (500) and return structured JSON responses.
- **Include context in error messages.** Bad: `"Order failed"`. Good: `"Contract qualification failed for AAPL: timeout after 20s"`.

## Concurrency Safety (MANDATORY)

- **Assume concurrency by default.** The bridge is async (aiohttp). Any handler can be interrupted at an `await`. Before merging any code touching shared state, ask: "Can two callers interleave here? What breaks?"
- **The `IBClient` is shared across all handlers.** The `ib` connection object is stored on the `IBClient` singleton, shared via `aiohttp.web.AppKey` across all concurrent requests. Do not store request-specific state on the client.
- **Financial operations require extra scrutiny.** Order placement, account mutation — review for races, double-execution, partial failure, idempotency.
- **Use `asyncio.get_running_loop()`, never `asyncio.get_event_loop()`.** `get_event_loop()` is deprecated since 3.10 and the behaviour will change to an error. Every call site that needs a loop runs *inside* one (sync `ib_async` callbacks dispatched on the running loop, async `IsolatedAsyncioTestCase` methods), so `get_running_loop()` always works. Tests patching `loop.call_later` must use `get_running_loop()` and be `async def` methods on `IsolatedAsyncioTestCase` — `get_event_loop()` in a sync test silently creates a fresh loop nobody runs, masking real bugs.
- **Schedule background tasks via `asyncio.get_running_loop().create_task(coro)`, not `asyncio.ensure_future(coro)`.** `ensure_future` falls back to `get_event_loop()` and may attach to a stale loop. Retain the returned `Task` in a tracking set (`self._background_tasks`) with `task.add_done_callback(set.discard)` to prevent GC mid-run.
- **Reconnection is asynchronous.** `on_disconnect()` schedules `_reconnect()` via `get_running_loop().create_task(...)` — handlers must check `client.is_connected` before performing operations and return 503 if disconnected.

## Dependency Management

- **All dependencies use exact pins (`==`).** Both runtime (`services/bridge/requirements.txt`) and dev (`requirements-dev.txt`). Builds must be reproducible — never `>=`, `~=`, or unpinned versions.
- **`requirements-dev.txt` contains only dev-only tools** (mypy, pytest, ruff). Runtime deps belong in service `requirements.txt`. Both installed together — never add a runtime dep to `requirements-dev.txt` (creates duplicate Dependabot PRs).
- **New dep** → pin immediately. Runtime: exact (`==`) in service file. Dev: exact (`==`) in dev file.

## Docker Image Version Bumps

Each image has a different risk profile for Dependabot bumps:

| Image | Risk | When to merge |
| --- | --- | --- |
| `ghcr.io/gnzsnz/ib-gateway` | **CRITICAL** | Never auto-merge. Manual review + E2E required. Changes can affect TWS API behaviour, IBC restart logic, 2FA handling, and the VNC RFB-banner health check. Check the upstream changelog against the autorestart file, IBC version, and port config before merging. |
| `python:3.11-slim` | **Medium** | Patch bumps within 3.11 are safe. Never bump to a different minor without running `make test`, `make typecheck`, `make e2e`. |
| `caddy:2-alpine` | **Medium** | Minor/patch within 2.x is generally safe — stable config format. Scan changelog for directive changes. |
| `theasp/novnc` | **Medium** | SHA digest bumps generally safe. Verify the RFB banner health check still passes. |
| `docker:XX-cli` | **Medium** | `monitor-gateway.sh` uses `{{.Actor.ID}}` (Docker 29+). Patch/minor within the same major are safe; major bumps need a Docker events API compatibility check. |
| `alpine:3.XX` | **Low** | Minor/patch safe. Major bumps require verifying all `apk add` packages still resolve. |
| `willfarrell/autoheal` | **Low** | SHA digest bumps safe to auto-merge — low-blast-radius watchdog. |

**Rule:** **Low** can be merged without manual testing. **Medium** requires a changelog review. **CRITICAL** requires manual E2E testing.

## Code Style

- Python: `logging` module, f-strings, `aiohttp` for the async HTTP server, `ib_async` for IB Gateway communication.
- CLI: stdlib only (`subprocess`, `urllib.request`, `json`, `os`). No third-party deps. Lazy dispatch via `importlib.import_module`.
- Terraform: secrets marked `sensitive = true` in `variables.tf`.

---

**Maintenance note.** Each directory `CLAUDE.md` has a Copilot mirror at `.github/instructions/<slug>.instructions.md` with an `applyTo:` glob — the slug is hyphenated path (`infra-gateway-controller`), not necessarily a single directory name. Root rules also mirror to `.github/copilot-instructions.md`. When editing any `CLAUDE.md`, update its mirror in the same commit. See [docs/INSTRUCTION_FILES.md](docs/INSTRUCTION_FILES.md) for the full layout and sync rules.
