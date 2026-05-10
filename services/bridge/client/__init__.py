"""IB Gateway client — connection management and namespace delegation."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Literal

from ib_async import IB
from ib_async import Trade as IBTrade
from ib_async.objects import CommissionReport, Fill, Position

from bridge_models import (
    WsComboLeg,
    WsCommissionReport,
    WsContract,
    WsDeltaNeutralContract,
    WsEventSource,
    WsExecution,
    WsFill,
    WsFillEnvelope,
    WsStatusEnvelope,
)
from client.event_hub import EventHub
from client.orders import OrdersNamespace
from client.trades import TradesNamespace

log = logging.getLogger("ib-client")

# Internal narrowed literals matching the discriminated WsEnvelope branches.
WsStatusType = Literal["connected", "disconnected"]
WsFillEventType = Literal["execDetailsEvent", "commissionReportEvent"]

CLIENT_ID = 1
INITIAL_RETRY_DELAY = 10
MAX_RETRY_DELAY = 300

# Window after (re)connect during which positionEvents from initial sync
# are ignored — otherwise every existing position triggers a reconcile.
INITIAL_SYNC_GRACE_SECONDS = 1.0

# Delay before reqExecutions in a reconcile, so the matching commissionReport
# message has time to land on the same Fill before we read it.
RECONCILE_SETTLE_SECONDS = 1.0


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
        self._initial_sync_complete = False
        self._reconcile_task: asyncio.Task[None] | None = None
        # execIds we've already broadcast as commissionReportEvent — used
        # to suppress reconcile-path re-broadcasts. Cleared on connect
        # (each daily session starts fresh; reqExecutions is current-day
        # only, so old execIds are dead weight).
        self._broadcast_exec_ids: set[str] = set()
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
        """Register ib_async event callbacks. Call once at startup.

        IB-level Event objects are created in IB.__init__ and persist
        across reconnects (wrapper.reset() does not touch them), so
        subscribing once is sufficient.
        """
        if self._events_subscribed:
            return
        self.ib.execDetailsEvent += self._on_exec_details
        self.ib.commissionReportEvent += self._on_commission_report
        self.ib.positionEvent += self._on_position
        self.ib.connectedEvent += self._on_connected
        self._events_subscribed = True

    def _broadcast_status(self, status: WsStatusType) -> None:
        envelope = WsStatusEnvelope(
            type=status,
            seq=0,  # Overwritten by hub.broadcast
            timestamp=datetime.now(UTC).isoformat(),
        )
        self.hub.broadcast(envelope.model_dump())

    def _on_connected(self) -> None:
        """Arm the initial-sync gate AND clear the daily exec-id set.

        ib_async fires positionEvent for each existing position right
        after connect; without the gate, every one would schedule a
        reqExecutions call. The exec-id set is cleared because
        reqExecutions only ever returns the current trading day —
        yesterday's execIds are dead weight after a daily session reset.
        """
        self._initial_sync_complete = False
        self._broadcast_exec_ids.clear()
        loop = asyncio.get_event_loop()
        loop.call_later(INITIAL_SYNC_GRACE_SECONDS, self._mark_synced)

    def _mark_synced(self) -> None:
        self._initial_sync_complete = True
        log.info("Initial position sync window closed — reconciliation armed")

    def _on_exec_details(self, trade: IBTrade, fill: Fill) -> None:
        # Deliberately NOT added to _broadcast_exec_ids — only commission
        # reports gate the reconcile path. If commissionReport never
        # arrives via the live callback (the permId2Trade gate in
        # ib_async can swallow it for completed external orders), the
        # reconcile path must still be free to fill the gap with full
        # commission data.
        self._broadcast_fill("execDetailsEvent", fill, source="live")

    def _on_commission_report(
        self, trade: IBTrade, fill: Fill, report: CommissionReport
    ) -> None:
        self._broadcast_fill(
            "commissionReportEvent", fill, report=report, source="live",
        )
        self._broadcast_exec_ids.add(fill.execution.execId)

    def _on_position(self, position: Position) -> None:
        """Schedule a reqExecutions reconcile when an account position changes.

        positionEvent fires across all users on the same account, including
        orders placed by another user (e.g., from mobile). reqExecutions is
        then the only path to surface those fills, since ib_async never emits
        execDetailsEvent for non-live (reqExecutions-derived) fills, and only
        emits commissionReportEvent when permId2Trade is populated — which
        it isn't for completed external orders after reconnect.
        """
        if not self._initial_sync_complete:
            return
        if self._reconcile_task and not self._reconcile_task.done():
            return  # coalesce: an in-flight reconcile already covers this
        task = asyncio.ensure_future(self._reconcile_executions())
        self._reconcile_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _reconcile_executions(self) -> None:
        """Pull today's fills via reqExecutions and broadcast new ones.

        Manual broadcast is required: ib_async does not emit
        execDetailsEvent for fills returned via reqExecutions (isLive
        gate), and only emits commissionReportEvent when the trade is
        in permId2Trade — which excludes completed orders from other
        users.

        Suppresses re-broadcasts via _broadcast_exec_ids: each execId
        is broadcast at most once per session, regardless of how many
        positionEvents fire later in the day. Live commissionReportEvent
        broadcasts also populate the set, so a fill the bridge already
        emitted live is skipped here. The RECONCILE_SETTLE_SECONDS
        delay gives the live commissionReport time to land first.
        """
        try:
            await asyncio.sleep(RECONCILE_SETTLE_SECONDS)
            fills = await self.ib.reqExecutionsAsync()
            new_count = 0
            for fill in fills:
                exec_id = fill.execution.execId
                if exec_id in self._broadcast_exec_ids:
                    continue
                self._broadcast_exec_ids.add(exec_id)
                self._broadcast_fill(
                    "commissionReportEvent", fill, source="reconciled",
                )
                new_count += 1
            log.info(
                "reconcile: %d new fill(s) broadcast (%d already seen)",
                new_count, len(fills) - new_count,
            )
        except Exception as exc:
            log.exception("reconcile failed: %s", exc)

    def _broadcast_fill(
        self,
        event_type: WsFillEventType,
        fill: Fill,
        *,
        report: CommissionReport | None = None,
        source: WsEventSource,
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

        envelope = WsFillEnvelope(
            type=event_type,
            seq=0,  # Overwritten by hub.broadcast
            timestamp=datetime.now(UTC).isoformat(),
            fill=ws_fill,
            source=source,
        )
        self.hub.broadcast(envelope.model_dump())
        log.info(
            "WS event: %s [%s] %s %s %.4g @ %.2f",
            event_type, source, ex.side, contract.symbol,
            ex.shares, ex.price,
        )
