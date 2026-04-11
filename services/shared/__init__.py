# Shared models — cross-project types consumed by external services.
#
# ibkr_relay has shared CommonFill models (Fill, Trade, WebhookPayload, etc.)
# that multiple consumers depend on. This module is the ibkr_bridge equivalent,
# reserved for shared/common types that belong to the primary "IbkrBridge"
# TypeScript namespace (see Namespace Convention in copilot-instructions.md).
#
# Bridge-specific types (HTTP API + WS events) live in bridge_models.py and
# export under the "IbkrBridgeHttp" namespace.
#
# When adding shared types here:
# 1. Define the models in this module.
# 2. Register them under the "shared" entry in schema_gen.py's SCHEMA_MODELS dict.
# 3. Create or update types/typescript/shared/ with index.d.ts + generated types.d.ts.
# 4. Update types/typescript/index.d.ts to export IbkrBridge from "./shared".
# 4. Update types/typescript/index.d.ts barrel to export IbkrBridge from "./shared".
# 5. Run `make types` to regenerate.
