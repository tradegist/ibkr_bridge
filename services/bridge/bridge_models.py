"""Public Pydantic models for the ibkr-bridge API.

!! PUBLIC CONTRACT — every type defined here is exported to consumers
!! via the generated TypeScript and Python type packages (make types).
!! Do NOT add bridge-internal helpers, validation logic, or intermediate
!! types here.  If you need a private model, put it in the module that
!! uses it (e.g. client/, bridge_routes/).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Strict union types aligned with ib_async ─────────────────────────

Action = Literal["BUY", "SELL"]

ExecSide = Literal["BOT", "SLD"]

OrderType = Literal["MKT", "LMT"]

SecType = Literal[
    "STK", "OPT", "FUT", "IND", "FOP", "CASH",
    "CFD", "BAG", "WAR", "BOND", "CMDTY", "NEWS",
    "FUND", "CRYPTO", "EVENT",
]

TimeInForce = Literal["DAY", "GTC", "IOC", "GTD", "OPG", "FOK", "DTC"]


# ── POST /ibkr/order ─────────────────────────────────────────────────

class ContractPayload(BaseModel):
    """Contract fields for identifying the instrument (mirrors ib_async.Contract)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    secType: SecType = "STK"
    exchange: str = "SMART"
    currency: str = "USD"
    primaryExchange: str = ""


class OrderPayload(BaseModel):
    """Order fields for specifying the trade (mirrors ib_async.Order)."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    totalQuantity: float = Field(gt=0)
    orderType: OrderType
    lmtPrice: float | None = None
    tif: TimeInForce = "DAY"
    outsideRth: bool = False


class PlaceOrderPayload(BaseModel):
    """Top-level request body for POST /ibkr/order."""

    model_config = ConfigDict(extra="forbid")

    contract: ContractPayload
    order: OrderPayload


class PlaceOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    orderId: int  # Permanent order ID (permId from ib_async)
    action: Action
    symbol: str
    totalQuantity: float
    orderType: OrderType
    lmtPrice: float | None = None


# ── GET /health ──────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    tradingMode: str


# ── GET /ibkr/trades ─────────────────────────────────────────────────

class FillDetail(BaseModel):
    """Single execution fill within a trade."""

    model_config = ConfigDict(extra="forbid")

    execId: str
    time: str
    exchange: str
    side: ExecSide
    shares: float
    price: float
    commission: float
    commissionCurrency: str
    realizedPNL: float


class TradeDetail(BaseModel):
    """A trade with its order info, status, and fills."""

    model_config = ConfigDict(extra="forbid")

    # Order identification
    orderId: int  # Permanent order ID (permId from ib_async)
    action: str  # str not Action — IB may return values beyond BUY/SELL for reads
    totalQuantity: float
    orderType: str  # str not OrderType — IB returns STP, TRAIL, etc. for existing orders
    lmtPrice: float | None = None
    tif: TimeInForce

    # Contract
    symbol: str
    secType: SecType
    exchange: str
    currency: str

    # Status
    status: str
    filled: float
    remaining: float
    avgFillPrice: float

    # Fills
    fills: list[FillDetail]


class ListTradesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: list[TradeDetail]


# ── WebSocket event streaming ────────────────────────────────────────
# Models mirror ib_async 2.1.0 dataclass fields exactly (same names,
# same nesting).  When bumping ib_async, update these models to match.

WsEventType = Literal[
    "execDetailsEvent",
    "commissionReportEvent",
    "connected",
    "disconnected",
]


class WsComboLeg(BaseModel):
    """Mirrors ib_async.contract.ComboLeg (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    conId: int
    ratio: int
    action: str
    exchange: str
    openClose: int
    shortSaleSlot: int
    designatedLocation: str
    exemptCode: int


class WsDeltaNeutralContract(BaseModel):
    """Mirrors ib_async.contract.DeltaNeutralContract (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    conId: int
    delta: float
    price: float


class WsContract(BaseModel):
    """Mirrors ib_async.contract.Contract (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    secType: str
    conId: int
    symbol: str
    lastTradeDateOrContractMonth: str
    strike: float
    right: str
    multiplier: str
    exchange: str
    primaryExchange: str
    currency: str
    localSymbol: str
    tradingClass: str
    includeExpired: bool
    secIdType: str
    secId: str
    description: str
    issuerId: str
    comboLegsDescrip: str
    comboLegs: list[WsComboLeg] = Field(default_factory=list)
    deltaNeutralContract: WsDeltaNeutralContract | None = None


class WsExecution(BaseModel):
    """Mirrors ib_async.objects.Execution (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    execId: str
    time: str
    acctNumber: str
    exchange: str
    side: str
    shares: float
    price: float
    permId: int
    clientId: int
    orderId: int
    liquidation: int
    cumQty: float
    avgPrice: float
    orderRef: str
    evRule: str
    evMultiplier: float
    modelCode: str
    lastLiquidity: int
    pendingPriceRevision: bool


class WsCommissionReport(BaseModel):
    """Mirrors ib_async.objects.CommissionReport (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    execId: str
    commission: float
    currency: str
    realizedPNL: float
    yield_: float
    yieldRedemptionDate: int


class WsFill(BaseModel):
    """Mirrors ib_async.objects.Fill NamedTuple (ib_async 2.1.0)."""

    model_config = ConfigDict(extra="forbid")

    contract: WsContract
    execution: WsExecution
    commissionReport: WsCommissionReport
    time: str


class WsEnvelope(BaseModel):
    """Top-level WebSocket message wrapper."""

    model_config = ConfigDict(extra="forbid")

    type: WsEventType
    seq: int
    timestamp: str
    fill: WsFill | None = None

