# IBKR Bridge — Project Guidelines

A Python/aiohttp bridge between IB Gateway and consumer services (`relayport` and others). Exposes a small REST + WebSocket API for placing orders, listing trades, and streaming fills.

These are the **always-on rules**. Path-scoped rules live in `.github/instructions/*.instructions.md` (loaded for matching files only). Architectural prose lives in [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Sibling Project: relayport

This project (`ibkr_bridge`) and its sibling `relayport` share the same CLI deploy/destroy/sync infrastructure pattern. Any change to `cli/core/deploy.py`, `cli/core/destroy.py`, or `cli/core/sync.py` here must be mirrored in `relayport`, and vice versa.

## Code Quality (MANDATORY)

- **Always apply best practices by default.** Use idiomatic Python naming, file organization, and patterns. When a clearly better approach exists, use it directly — don't ask permission.
- **NEVER use deprecated APIs.** Examples: `asyncio.get_event_loop()` → `asyncio.get_running_loop()`; `datetime.utcnow()` → `datetime.now(UTC)`; Pydantic v1 `parse_obj` / `dict()` → v2 `model_validate` / `model_dump`. Scan docs for "deprecated" before relying on anything new. A deprecation warning in CR is a regression — fix the call, don't suppress.
- **No unused imports.** After any edit, verify every `import` is used. Remove what isn't.
- **No `__all__`.** All imports are explicit (`from module import X`); star-imports are never used. `make lint` has a hard fail that greps for `__all__`.
- **No `assert` for runtime guards.** `assert` is stripped under `python -O`. Use `if … raise RuntimeError(...)` (or `die()`) for invariants that must hold at runtime.
- **Run `make lint` after every code change.** Ruff enforces unused imports, import ordering, unused variables, bugbear pitfalls, modern idioms. `make lint FIX=1` auto-fixes safe issues.
- **Centralise env var reads into typed getter functions.** Each env var is read in exactly one place — a getter in the module that owns it. Getters apply `.strip()` and type conversion. Never call `os.environ.get()` inline outside a getter.
- **Getters must validate and fail fast.** Every getter must validate and `raise SystemExit("<descriptive message>")` on bad input. Wrap `int()`/`float()` in `try/except ValueError`. Check emptiness on required strings. Callers should never have to validate.
- **Prefer pure functions over side-effect functions.** Compute and return values; let the caller decide. If unavoidable, add an inline comment at every call site: `# Mutates X to enable Y`.
- **Never bulk-set `os.environ` with empty-string fallbacks.** Silently overrides downstream defaults. Only export when source is present and non-empty; `os.environ.pop(key, None)` otherwise.
- **Verify Markdown table integrity after every edit.** Count column dividers on changed row(s) AND header/separator rows — all must match. Sanity check: `awk -F'\|' 'NR>=START && NR<=END { print NR": "NF" cells" }' file.md`.
- **Update README.md when changing public interfaces.** CLI commands, Makefile targets, API endpoints, env vars.
- **Register new modules in `pyproject.toml`** (`testpaths`, `tool.ruff.src`, `known-first-party`) and the Makefile `lint:`/`typecheck:` targets.

## Security Rules (MANDATORY)

- **No hardcoded credentials.** Use env vars (`.env`, `TF_VAR_*`). Never real values in source.
- **No hardcoded IPs.** Use `DROPLET_IP`. In docs use `1.2.3.4` as placeholder.
- **No hardcoded domains.** Use `example.com` variants (`trade.example.com`, `vnc.example.com`); runtime via `SITE_DOMAIN` / `VNC_DOMAIN`.
- **No email addresses or personal info.** Never real names, emails, or account IDs in committed files.
- **No developer-machine paths.** Never `/Users/john/…` or `C:\Users\john\…`. Reference sibling projects by name only.
- **No logging of secrets or sensitive operational data.** Never `log.info()` tokens, passwords, keys, account IDs, IPs, or domains. Log actions and outcomes — counts, symbols, statuses, not full objects.
- **`.env`, `.env.droplet`, `.env.test`, `*.tfvars` are gitignored.** Use `env_examples/` templates with placeholders.
- **Terraform state is gitignored** — `terraform.tfstate` contains SSH keys and IPs.
- **Auth middleware must reject empty `API_TOKEN`.** `hmac.compare_digest("", "")` returns `True`, so empty `API_TOKEN` silently disables auth. Check `if not api_token: return HTTP 500` **before** `compare_digest`. Do not rely on `required_env` here: that validation is CLI/deploy-path specific (for example, standalone deploy), not a universal guarantee across all sync/shared flows.

## Type Safety (MANDATORY)

- **Python >= 3.11.** Uses `X | None` natively (no `from __future__ import annotations`). Docker uses `python:3.11-slim`.
- **Run `make typecheck`, `make test`, and `make lint` after every code change.** Non-negotiable before deploying. mypy + ruff + pytest must all pass. `make typecheck` also runs `tsc --noEmit` on `types/typescript/`.
- **Run E2E tests after modifying any E2E test OR infrastructure file** (`docker-compose*.yml`, `Dockerfile`, `Caddyfile`, anything under `infra/`). Workflow: `make e2e-up` (waits up to 240s) → `make e2e-run` → fix → repeat → `make e2e-down` only after pass.
- **Every Python file must be covered by `make typecheck`.** New module → add to the mypy invocation in the Makefile.
- After modifying any model in `services/bridge/bridge_models.py`, run `make types`.
- **Always verify type safety by breaking it first.** After refactoring types, introduce a deliberate type error, run `make typecheck`, confirm it **fails**. Then revert. Never assume mypy catches something — prove it.
- **Avoid `dict[str, Any]` round-trips.** No `model_dump()` → `dict` → `Model(**data)`. Use explicit kwargs or `model_copy(update=...)`.
- **Prefer strict `Literal` types over bare `str` on Pydantic models.** Use `Action`, `OrderType`, `SecType`, `TimeInForce`, `ExecSide` when the value set is known. Fall back to `str` only when IB Gateway genuinely returns unbounded values — document why (see `TradeDetail.action`, `TradeDetail.orderType`).
- **No `# type: ignore` without justification.** Fix the root cause. Suppression must include a reason: `# type: ignore[attr-defined] # ib_async.Foo has no stubs`.
- **Use `cast()` instead of `# type: ignore[arg-type]`.** Preserves downstream type-checking; `# type: ignore` silently disables it.
- **Use `cast()` for `ib_async` values.** No stubs. Use `cast(ExecSide, ex.side)` etc.
- **Use `@overload` for sentinel-default patterns.** Express the two signatures via `@overload`, not `# type: ignore`.

## Pydantic Best Practices

- **`Field(default_factory=list)`** for mutable defaults — only when genuinely optional. Never bare `[]` or `{}`.
- **No defaults on always-populated fields.** A default makes the field optional in the generated JSON Schema / TS.
- **`ConfigDict(extra="forbid")`** on external-contract models (API requests/responses, WS envelopes).

## Error Handling (MANDATORY)

- **Every error must produce a clear, actionable message.** Include context: operation, input identifier, upstream status.
- **API responses must never leak internal details.** Structured error JSON with appropriate HTTP status. Never tracebacks, paths, or class names.
- **Isolate failures.** The bridge has multiple concerns (connection, orders, trades, WS broadcast). A failure in one must not take down others.
- **Never silently swallow errors.** Every `except` must `log.exception(...)` or re-raise. Bare `except: pass` is never acceptable.
- **`log.exception()` for unexpected errors** (auto-includes traceback at ERROR).
- **Distinguish recoverable from fatal.** Connection losses are recoverable (auto-reconnect). Missing config → `raise SystemExit(msg)`.
- **`SystemExit` must carry a descriptive message.** Never `raise SystemExit(1)`.
- **Env var parsing must fail fast.** Wrap `int()`/`float()` in `try/except ValueError: raise SystemExit(...)`. Fall back only on _missing_ vars, never on _invalid_ values.
- **Validate at system boundaries, trust internally.** Validate API payloads, env vars, IB Gateway responses at entry.
- **Never assume a default for financial enum fields.** Validate exactly. For read-only fields with unbounded values (`TradeDetail.action`, `TradeDetail.orderType`), use `str` with an inline comment.
- **HTTP handlers must catch and map exceptions.** Distinguish `ValueError` (400) from `RuntimeError` (500); return structured JSON.
- **Include context in error messages.** Bad: `"Order failed"`. Good: `"Contract qualification failed for AAPL: timeout after 20s"`.

## Concurrency Safety (MANDATORY)

- **Assume concurrency by default.** The bridge is async (aiohttp). Any handler can be interrupted at an `await`. Before merging any code touching shared state, ask: "Can two callers interleave?"
- **The `IBClient` is shared across all handlers** via `aiohttp.web.AppKey`. Do not store request-specific state on the client.
- **Financial operations require extra scrutiny.** Review for races, double-execution, partial failure, idempotency.
- **Use `asyncio.get_running_loop()`, never `asyncio.get_event_loop()`** (deprecated since 3.10). Tests patching `loop.call_later` must use `get_running_loop()` and be `async def` methods on `IsolatedAsyncioTestCase`.
- **Schedule background tasks via `asyncio.get_running_loop().create_task(coro)`**, not `asyncio.ensure_future(coro)`. Retain the returned `Task` in `self._background_tasks` with `task.add_done_callback(set.discard)`.
- **Reconnection is asynchronous.** Handlers must check `client.is_connected` before operations and return 503 if disconnected.

## Dependency Management

- **All deps use exact pins (`==`).** Both runtime and dev. Reproducible builds — no `>=`, `~=`, unpinned.
- **`requirements-dev.txt` contains only dev-only tools.** Runtime deps belong in service `requirements.txt`. Never duplicate.
- **New dep** → pin immediately to an exact version.

## Docker Image Version Bumps

| Image | Risk | When to merge |
| --- | --- | --- |
| `ghcr.io/gnzsnz/ib-gateway` | **CRITICAL** | Never auto-merge. Manual review + E2E required. Check upstream changelog against autorestart file, IBC version, port config. |
| `python:3.11-slim` | **Medium** | Patch within 3.11 safe. Never minor bump without `make test`, `make typecheck`, `make e2e`. |
| `caddy:2-alpine` | **Medium** | Minor/patch within 2.x generally safe — scan changelog for directive changes. |
| `theasp/novnc` | **Medium** | SHA digest bumps generally safe. Verify RFB banner health check still passes. |
| `docker:XX-cli` | **Medium** | `monitor-gateway.sh` uses `{{.Actor.ID}}` (Docker 29+). Patch/minor safe; major needs Docker events API check. |
| `alpine:3.XX` | **Low** | Minor/patch safe. Major requires verifying `apk add` packages resolve. |
| `willfarrell/autoheal` | **Low** | SHA digest bumps safe to auto-merge. |

**Rule:** **Low** merges without manual testing. **Medium** requires changelog review. **CRITICAL** requires manual E2E.

## Code Style

- Python: `logging` module, f-strings, `aiohttp` for async HTTP, `ib_async` for IB Gateway.
- CLI: stdlib only. No third-party deps. Lazy dispatch via `importlib.import_module`.
- Terraform: secrets marked `sensitive = true` in `variables.tf`.

## Maintenance of this file

This file is the always-on Copilot mirror of the root `CLAUDE.md` (Claude Code's repo-wide instruction file). Per-directory rules also have paired mirrors at `.github/instructions/<slug>.instructions.md` with `applyTo:` globs. **When editing any `CLAUDE.md`, update its corresponding Copilot mirror in the same commit** — rule content must stay in sync; presentation may differ slightly (see `docs/INSTRUCTION_FILES.md` for the full layout and the allowed divergences).
