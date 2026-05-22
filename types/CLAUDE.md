# `types/` — Generated type packages

TypeScript + Python type packages generated from `services/bridge/bridge_models.py`. **Do not hand-edit files marked `AUTO-GENERATED`.**

For the rule "to export a new model to TypeScript, add it to `schema_gen.py:SCHEMA_MODELS`", see the [`export-new-model-to-types`](../.claude/skills/export-new-model-to-types/SKILL.md) skill.

## Sources → generated files

| Source Pydantic | TypeScript output | Python output |
|---|---|---|
| `services/bridge/bridge_models.py` | `types/typescript/http/types.d.ts` | `types/python/ibkr_bridge_types/models.py` |

Run `make types` after any model change. Today only `bridge_models.py` is registered in `SCHEMA_MODELS`. When `services/shared/` gains real models, register them under a new `"shared"` key.

## TypeScript namespace convention (cross-project standard)

All projects export TypeScript types using a two-tier namespace pattern:

- **`types/typescript/`** → exported as the **project's primary namespace** (e.g. `IbkrBridge`). Reserved for shared/common types that multiple consumers depend on. Currently empty — no shared types yet.
- **`types/typescript/<module>/`** → exported as **`<ProjectName><ModuleName>`** (e.g. `IbkrBridgeHttp`). Contains module-specific types generated from that module's `SCHEMA_MODELS` entry.

**`IbkrBridge` is reserved for the primary/shared namespace — do not use it for module-specific types.**

The hand-maintained barrel `types/typescript/index.d.ts` currently exports only the HTTP namespace:

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

## TypeScript package (npm)

- Published as `@tradegist/ibkr-bridge-types` (not yet published).
- **One namespace today**: `IbkrBridgeHttp` (HTTP API + WS event types).
- Generated files: `types/typescript/http/types.d.ts` + auto-generated `index.d.ts` barrel (via `gen_ts_barrels.py`).
- `package.json` `files:` must include every shipped namespace directory. Today: `["index.d.ts", "http/"]`. When `shared/` is added, append `"shared/"` to `files:` — otherwise the barrel's `import * as IbkrBridge from "./shared"` would resolve to a missing path when the package is packed. Verify with `npm pack --dry-run` after any `files:` change.
- **Usage:** `import { IbkrBridgeHttp } from "@tradegist/ibkr-bridge-types"`.

## Python types package (PyPI)

- Published as `ibkr-bridge-types` (not yet published).
- **Standalone Pydantic models** — no dependency on `ib_async` or the bridge service.
- Exports the **same public types** as the TypeScript package: HTTP API models, WS event models, Literal aliases.
- **Auto-generated** by `gen_python_types.py`:
  - `models.py` mirrors `bridge_models.py` verbatim (with import rewrites).
  - `__init__.py` barrel is built by AST-walking the source for top-level public class / Literal / alias definitions.
- **Usage:** `from ibkr_bridge_types import PlaceOrderPayload, WsEnvelope, Action`.
- **Covered by `make lint` and `make typecheck`** — `types/python/ibkr_bridge_types/` is included in both. Generated code must pass ruff and mypy.

## Authoring rules

- **Do not hand-edit generated files.** They carry an `AUTO-GENERATED` header. Edit `services/bridge/bridge_models.py` instead, then `make types`.
- The **hand-maintained** files are:
  - `types/typescript/index.d.ts` (top-level barrel — only changes when adding a new namespace).
  - `types/typescript/package.json` (npm manifest — `files:` must include every shipped namespace dir).
  - `types/python/pyproject.toml` (PyPI manifest).
- The module barrels inside `types/typescript/<ns>/index.d.ts` and `types/python/ibkr_bridge_types/__init__.py` are **auto-generated**.
- `schema_gen.py:SCHEMA_MODELS` is typed `dict[str, list[str]]` and keyed by importable module name (`"bridge_models"`). To export a new model, add the symbol's **name as a string** (e.g. `"PlaceOrderPayload"`, not `PlaceOrderPayload`) to the relevant list — `schema_gen.py` resolves the name via `getattr(module, name)` at generation time. The resolved value may be a `BaseModel` subclass, a discriminated-union `TypeAlias` (e.g. `WsEnvelope`), or a class alias.
- **When bumping `ib_async`**, update the WS mirror models in `bridge_models.py` (`WsContract`, `WsExecution`, `WsCommissionReport`, `WsComboLeg`, `WsDeltaNeutralContract`) to match the upstream shapes, then `make types`. See the `bump-ib-async-version` skill.
