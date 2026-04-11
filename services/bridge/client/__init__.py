"""IB Gateway client — connection management and namespace delegation."""

import asyncio
import logging
import os
from datetime import UTC, datetime

from ib_async import IB
from ib_async import Trade as IBTrade
from ib_async.objects import CommissionReport, Fill

from bridge_models import (
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
from client.event_hub import EventHub
from client.orders import OrdersNamespace
from client.trades import TradesNamespace

log = logging.getLogger("ib-client")

CLIENT_ID = 1
INITIAL_RETRY_DELAY = 10
MAX_RETRY_DELAY = 300


def get_ib_host() -> str:
    return os.environ.get("IB_HOST", "ib-gateway").strip()


def get_trading_mode() -> str:
    mode = os.environ.get("TRADING_MODE", "paper").strip()
    if mode not in ("paper", "live"):
        raise SystemExit(
            f"Invalid TRADING_MODE={mode!r} — must be 'paper' or 'live'"
        )
    return mode


def get_ib_port() -> int:
    mode = get_trading_mode()
    if mode == "live":
        var, default = "IB_LIVE_PORT", "4003"
    else:
        var, default = "IB_PAPER_PORT", "4004"
    raw = os.environ.get(var, default).strip()
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid {var}={raw!r} — must be an integer"
        ) from None


class IBClient:
    """Thin wrapper around ib_async.IB for connection management."""

    def __init__(self, hub: EventHub) -> None:
        self.ib = IB()
        self.hub = hub
        self._retry_delay = INITIAL_RETRY_DELAY
        self._connect_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._events_subscribed = False
        self.orders = OrdersNamespace(self.ib)
        self.trades = TradesNamespace(self.ib)

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

    async def connect(self) -> None:
        """Connect to IB Gateway with exponential backoff retry.

        Serialized via _connect_lock so concurrent callers (watchdog,
        _reconnect) await the in-flight attempt instead of starting a
        parallel retry loop.
        """
        async with self._connect_lock:
            if self.is_connected:
                return
            ib_host = get_ib_host()
            ib_port = get_ib_port()
            while True:
                try:
                    log.info("Connecting to IB Gateway at %s:%d ...", ib_host, ib_port)
                    await self.ib.connectAsync(
                        ib_host, ib_port, clientId=CLIENT_ID, timeout=20
                    )
                    log.info(
                        "Connected — %d account(s)", len(self.ib.managedAccounts())
                    )
                    self._retry_delay = INITIAL_RETRY_DELAY
                    self._broadcast_status("connected")
                    return
                except Exception as exc:
                    log.warning(
                        "Connection failed: %s — retrying in %ds",
                        exc, self._retry_delay,
                    )
                    await asyncio.sleep(self._retry_delay)
                    self._retry_delay = min(self._retry_delay * 2, MAX_RETRY_DELAY)

    def on_disconnect(self) -> None:
        log.warning("Disconnected from IB Gateway — will reconnect")
        self._broadcast_status("disconnected")
        task = asyncio.ensure_future(self._reconnect())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _reconnect(self) -> None:
        await asyncio.sleep(self._retry_delay)
        if not self.is_connected:
            await self.connect()

    async def watchdog(self) -> None:
        """Periodically check the connection and reconnect if stale."""
        while True:
            await asyncio.sleep(30)
            if not self.is_connected:
                log.warning("Watchdog: connection lost — reconnecting")
                await self.connect()

    # ── ib_async event wiring ────────────────────────────────────────

    def subscribe_events(self) -> None:
        """Register ib_async event callbacks. Call once after connect."""
        if self._events_subscribed:
            return
        self.ib.execDetailsEvent += self._on_exec_details
        self.ib.commissionReportEvent += self._on_commission_report
        self._events_subscribed = True

    def _broadcast_status(self, status: WsStatusType) -> None:
        envelope = WsEnvelope(
            type=status,
            seq=0,  # Overwritten by hub.broadcast
            timestamp=datetime.now(UTC).isoformat(),
        )
        self.hub.broadcast(envelope.model_dump())

    def _on_exec_details(self, trade: IBTrade, fill: Fill) -> None:
        self._broadcast_fill("execDetailsEvent", trade, fill)

    def _on_commission_report(
        self, trade: IBTrade, fill: Fill, report: CommissionReport
    ) -> None:
        self._broadcast_fill(
            "commissionReportEvent", trade, fill, report=report,
        )

    def _broadcast_fill(
        self,
        event_type: WsEventType,
        trade: IBTrade,
        fill: Fill,
        *,
        report: CommissionReport | None = None,
    ) -> None:
        ex = fill.execution
        contract = fill.contract
        cr = report if report else fill.commissionReport

        ws_contract = WsContract(
            secType=contract.secType,
            conId=contract.conId,
            symbol=contract.symbol,
            lastTradeDateOrContractMonth=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            includeExpired=contract.includeExpired,
            secIdType=contract.secIdType,
            secId=contract.secId,
            description=contract.description,
            issuerId=contract.issuerId,
            comboLegsDescrip=contract.comboLegsDescrip,
            comboLegs=[
                WsComboLeg(
                    conId=leg.conId,
                    ratio=leg.ratio,
                    action=leg.action,
                    exchange=leg.exchange,
                    openClose=leg.openClose,
                    shortSaleSlot=leg.shortSaleSlot,
                    designatedLocation=leg.designatedLocation,
                    exemptCode=leg.exemptCode,
                ) for leg in contract.comboLegs
            ],
            deltaNeutralContract=(
                WsDeltaNeutralContract(
                    conId=contract.deltaNeutralContract.conId,
                    delta=contract.deltaNeutralContract.delta,
                    price=contract.deltaNeutralContract.price,
                )
                if contract.deltaNeutralContract
                else None
            ),
        )

        ws_execution = WsExecution(
            execId=ex.execId,
            time=ex.time.isoformat() if ex.time else "",
            acctNumber=ex.acctNumber,
            exchange=ex.exchange,
            side=ex.side,
            shares=ex.shares,
            price=ex.price,
            permId=ex.permId,
            clientId=ex.clientId,
            orderId=ex.orderId,
            liquidation=ex.liquidation,
            cumQty=ex.cumQty,
            avgPrice=ex.avgPrice,
            orderRef=ex.orderRef,
            evRule=ex.evRule,
            evMultiplier=ex.evMultiplier,
            modelCode=ex.modelCode,
            lastLiquidity=ex.lastLiquidity,
            pendingPriceRevision=ex.pendingPriceRevision,
        )

        ws_commission = WsCommissionReport(
            execId=cr.execId,
            commission=cr.commission,
            currency=cr.currency,
            realizedPNL=cr.realizedPNL,
            yield_=cr.yield_,
            yieldRedemptionDate=cr.yieldRedemptionDate,
        )

        ws_fill = WsFill(
            contract=ws_contract,
            execution=ws_execution,
            commissionReport=ws_commission,
            time=fill.time.isoformat() if fill.time else "",
        )

        envelope = WsEnvelope(
            type=event_type,
            seq=0,  # Overwritten by hub.broadcast
            timestamp=datetime.now(UTC).isoformat(),
            fill=ws_fill,
        )
        self.hub.broadcast(envelope.model_dump())
        log.info(
            "WS event: %s %s %s %.4g @ %.2f",
            event_type, ex.side, contract.symbol,
            ex.shares, ex.price,
        )
