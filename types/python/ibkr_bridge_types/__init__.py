"""ibkr-bridge-types — Typed Pydantic models for the ibkr-bridge API.

HTTP API types (order placement, trades, health) and WebSocket event
models.  No dependency on ib_async — pure Pydantic.
"""

from ibkr_bridge_types.models import (
    Action,
    ContractPayload,
    ExecSide,
    FillDetail,
    HealthResponse,
    ListTradesResponse,
    OrderPayload,
    OrderType,
    PlaceOrderPayload,
    PlaceOrderResponse,
    SecType,
    TimeInForce,
    TradeDetail,
    WsComboLeg,
    WsCommissionReport,
    WsContract,
    WsDeltaNeutralContract,
    WsEnvelope,
    WsEventType,
    WsExecution,
    WsFill,
    WsStatusType,
)

__all__ = [
    # Type aliases
    "Action",
    "ExecSide",
    "OrderType",
    "SecType",
    "TimeInForce",
    "WsEventType",
    "WsStatusType",
    # HTTP API models
    "ContractPayload",
    "FillDetail",
    "HealthResponse",
    "ListTradesResponse",
    "OrderPayload",
    "PlaceOrderPayload",
    "PlaceOrderResponse",
    "TradeDetail",
    # WebSocket event models
    "WsComboLeg",
    "WsCommissionReport",
    "WsContract",
    "WsDeltaNeutralContract",
    "WsEnvelope",
    "WsExecution",
    "WsFill",
]
