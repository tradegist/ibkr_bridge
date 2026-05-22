---
name: bump-ib-async-version
description: Update the WS mirror models in bridge_models.py when ib_async is bumped, and regenerate the type packages. Use when the user bumps the ib_async pin in services/bridge/requirements.txt (Dependabot PR or manual), or asks "how do I update for ib_async X.Y.Z".
---

# Bumping `ib_async`

`ib_async` is the IBKR Gateway client library. The bridge surfaces five `ib_async` data shapes through the WebSocket event stream:

- `Contract` → `WsContract`
- `Execution` → `WsExecution`
- `CommissionReport` → `WsCommissionReport`
- `ComboLeg` → `WsComboLeg`
- `DeltaNeutralContract` → `WsDeltaNeutralContract`

These live in `services/bridge/bridge_models.py`. **They mirror the upstream `ib_async` 2.1.0 shapes exactly.** When `ib_async` bumps, the mirror models must be checked and updated, or downstream consumers (`@tradegist/ibkr-bridge-types`, `ibkr-bridge-types`) will silently drift away from runtime reality.

## Procedure

1. **Read the `ib_async` changelog** for the version range you're crossing. Focus on:
   - Field additions or removals on `Contract`, `Execution`, `CommissionReport`, `ComboLeg`, `DeltaNeutralContract`.
   - Field renames or type changes on those classes.
   - Any new Literal-style enums (e.g. new `secType` values) that should be reflected in `Action`, `ExecSide`, `OrderType`, `SecType`, `TimeInForce`.

2. **Inspect the new shapes in the installed package**. After `pip install -r requirements-dev.txt -r services/bridge/requirements.txt`:

   ```bash
   .venv/bin/python -c "from ib_async import Contract, Execution, CommissionReport, ComboLeg, DeltaNeutralContract; import dataclasses; [print(c.__name__, [(f.name, f.type) for f in dataclasses.fields(c)]) for c in [Contract, Execution, CommissionReport, ComboLeg, DeltaNeutralContract]]"
   ```

   Compare against the `Ws*` models in `services/bridge/bridge_models.py`. Add, remove, or retype fields to match.

3. **Update the `# Mirrors ib_async.Foo (ib_async X.Y.Z)` comments** in `bridge_models.py` to the new version.

4. **Update the version-attribution comments in `docs/ARCHITECTURE.md`** (the "Public model inventory" table notes "Mirrors `ib_async.X` (2.1.0)").

5. **Run the full check pipeline:**
   ```bash
   make lint
   make typecheck
   make test
   ```
   The test suite includes WS event-shape tests that exercise the mirror models against real `ib_async` instances — failures here often mean the mirror is now stale.

6. **Run `make types`** to regenerate the TypeScript + Python type packages.

7. **Inspect the generated diff**:
   ```bash
   git diff types/typescript/http/ types/python/ibkr_bridge_types/
   ```
   Confirm the changes are intentional. Verify with `npm pack --dry-run` in `types/typescript/` that everything still packs.

8. **Run E2E tests** if the bump touches anything related to execDetails, commissionReport, or position events:
   ```bash
   make e2e-up
   make e2e-run
   ```
   See `services/bridge/CLAUDE.md` for the reconcile invariants — those test paths exercise the `Ws*` shapes against real Gateway events.

9. **Bump the version in `services/bridge/requirements.txt`** (or accept the Dependabot bump). All deps use exact pins (`==`).

## Common pitfalls

- **`ib_async` has no type stubs.** mypy treats attribute access as `Any`. The mirror models don't catch upstream renames at typecheck time — you have to read the changelog and inspect the installed package. Don't trust the test suite alone to catch additions.
- **`cast()` for ib_async values** — when mapping `ib_async` instances to the mirror models, use `cast(ExecSide, ex.side)` rather than `# type: ignore`. If a new Literal value appears in the upstream (e.g. a new `secType`), the cast will silently widen — also update the `Literal` alias in `bridge_models.py`.
- **`TradeDetail.action` and `TradeDetail.orderType` are intentionally `str`**, not the constrained Literals, because Gateway returns values beyond our aliases (`STP`, `TRAIL`, …). Don't tighten these to Literals without checking real Gateway responses.
- **`WsEnvelope` is a `TypeAlias` discriminated union** — Pydantic's `TypeAdapter` handles it via `Annotated[A | B, Field(discriminator="type")]`. Don't change `WsEnvelope` to a `BaseModel` subclass; consumers validate raw dicts with `TypeAdapter(WsEnvelope).validate_python(data)`.
