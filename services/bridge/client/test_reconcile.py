"""Unit tests for the positionEvent → reqExecutions reconcile path."""

import asyncio
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from ib_async import IB

from client import IBClient
from client.event_hub import EventHub


def _mock_fill(
    exec_id: str = "EX001",
    symbol: str = "AAPL",
    shares: float = 1.0,
    price: float = 150.0,
) -> MagicMock:
    """Build a Fill mock with the attributes _broadcast_fill reads."""
    fill = MagicMock()
    fill.execution.execId = exec_id
    fill.execution.time = None
    fill.execution.acctNumber = "U1"
    fill.execution.exchange = "NASDAQ"
    fill.execution.side = "BOT"
    fill.execution.shares = shares
    fill.execution.price = price
    fill.execution.permId = 1
    fill.execution.clientId = 0
    fill.execution.orderId = 0
    fill.execution.liquidation = 0
    fill.execution.cumQty = shares
    fill.execution.avgPrice = price
    fill.execution.orderRef = ""
    fill.execution.evRule = ""
    fill.execution.evMultiplier = 0.0
    fill.execution.modelCode = ""
    fill.execution.lastLiquidity = 1
    fill.execution.pendingPriceRevision = False

    fill.contract.secType = "STK"
    fill.contract.conId = 1
    fill.contract.symbol = symbol
    fill.contract.lastTradeDateOrContractMonth = ""
    fill.contract.strike = 0.0
    fill.contract.right = ""
    fill.contract.multiplier = ""
    fill.contract.exchange = "SMART"
    fill.contract.primaryExchange = ""
    fill.contract.currency = "USD"
    fill.contract.localSymbol = symbol
    fill.contract.tradingClass = "NMS"
    fill.contract.includeExpired = False
    fill.contract.secIdType = ""
    fill.contract.secId = ""
    fill.contract.description = ""
    fill.contract.issuerId = ""
    fill.contract.comboLegsDescrip = ""
    fill.contract.comboLegs = []
    fill.contract.deltaNeutralContract = None

    fill.commissionReport.execId = exec_id
    fill.commissionReport.commission = 1.0
    fill.commissionReport.currency = "USD"
    fill.commissionReport.realizedPNL = 0.0
    fill.commissionReport.yield_ = 0.0
    fill.commissionReport.yieldRedemptionDate = 0

    fill.time = None
    return fill


def _make_client(req_executions_return: list[Any] | None = None) -> IBClient:
    """IBClient with the IB instance replaced by a MagicMock.

    Casting via setattr keeps mypy happy while letting tests stub
    individual ib_async methods like reqExecutionsAsync.
    """
    hub = EventHub(buffer_size=100, max_subscribers=5)
    client = IBClient(hub)
    ib_mock = MagicMock(spec=IB)
    ib_mock.reqExecutionsAsync = AsyncMock(return_value=req_executions_return or [])
    client.ib = ib_mock
    return client


def _ib(client: IBClient) -> Any:
    """Return the (mocked) ib attribute, untyped, for stubbing in tests."""
    return client.ib


async def _await_reconcile(client: IBClient) -> None:
    """Await the in-flight reconcile task, with a None-guard for mypy."""
    task = client._reconcile_task
    if task is None:
        raise RuntimeError("expected a reconcile task to be scheduled")
    await task


class TestInitialSyncGate(unittest.IsolatedAsyncioTestCase):
    async def test_position_event_ignored_before_sync_complete(self) -> None:
        client = _make_client()
        # _initial_sync_complete defaults to False
        client._on_position(MagicMock())
        self.assertIsNone(client._reconcile_task)

    async def test_mark_synced_arms_reconcile(self) -> None:
        client = _make_client()
        self.assertFalse(client._initial_sync_complete)
        client._mark_synced()
        self.assertTrue(client._initial_sync_complete)

    async def test_on_connected_resets_sync_and_schedules_mark(self) -> None:
        client = _make_client()
        client._initial_sync_complete = True  # pretend we were armed
        with patch.object(asyncio.get_running_loop(), "call_later") as call_later:
            client._on_connected()
        self.assertFalse(client._initial_sync_complete)
        call_later.assert_called_once()
        delay, fn = call_later.call_args.args
        self.assertEqual(fn, client._mark_synced)
        self.assertGreater(delay, 0)


class TestReconcileScheduling(unittest.IsolatedAsyncioTestCase):
    async def test_position_event_schedules_reconcile_when_armed(self) -> None:
        client = _make_client()
        client._initial_sync_complete = True
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            client._on_position(MagicMock())
            self.assertIsNotNone(client._reconcile_task)
            await _await_reconcile(client)
        _ib(client).reqExecutionsAsync.assert_awaited_once()

    async def test_concurrent_position_events_coalesce(self) -> None:
        client = _make_client()
        client._initial_sync_complete = True
        # Slow reqExecutions so the first task is still in flight when the
        # second event fires.
        gate = asyncio.Event()

        async def slow_req() -> list[Any]:
            await gate.wait()
            return []

        _ib(client).reqExecutionsAsync = AsyncMock(side_effect=slow_req)
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            client._on_position(MagicMock())  # schedules task #1
            first = client._reconcile_task
            client._on_position(MagicMock())  # should be coalesced
            self.assertIs(client._reconcile_task, first)
            client._on_position(MagicMock())  # also coalesced
            self.assertIs(client._reconcile_task, first)
            gate.set()
            await _await_reconcile(client)
        # Only one reqExecutions call despite 3 position events
        self.assertEqual(_ib(client).reqExecutionsAsync.await_count, 1)

    async def test_new_event_after_completion_schedules_again(self) -> None:
        client = _make_client()
        client._initial_sync_complete = True
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            client._on_position(MagicMock())
            await _await_reconcile(client)
            client._on_position(MagicMock())
            await _await_reconcile(client)
        self.assertEqual(_ib(client).reqExecutionsAsync.await_count, 2)


class TestReconcileBroadcast(unittest.IsolatedAsyncioTestCase):
    async def test_broadcasts_one_event_per_fill(self) -> None:
        fills = [
            _mock_fill(exec_id="A", symbol="MSTR", shares=100, price=185.96),
            _mock_fill(exec_id="B", symbol="TSLA", shares=25, price=414.8),
        ]
        client = _make_client(req_executions_return=fills)
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            await client._reconcile_executions()
        events = client.hub.replay(0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "commissionReportEvent")
        self.assertEqual(events[0]["source"], "reconciled")
        self.assertEqual(events[1]["type"], "commissionReportEvent")
        self.assertEqual(events[1]["source"], "reconciled")
        # Verify the execIds got through
        exec_ids = {
            cast(dict[str, Any], cast(dict[str, Any], e["fill"])["execution"])["execId"]
            for e in events
        }
        self.assertEqual(exec_ids, {"A", "B"})

    async def test_no_fills_broadcasts_nothing(self) -> None:
        client = _make_client()
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            await client._reconcile_executions()
        self.assertEqual(len(client.hub.replay(0)), 0)

    async def test_reqexecutions_failure_does_not_propagate(self) -> None:
        client = _make_client()
        _ib(client).reqExecutionsAsync = AsyncMock(
            side_effect=RuntimeError("gateway down"),
        )
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            # Should not raise
            await client._reconcile_executions()
        self.assertEqual(len(client.hub.replay(0)), 0)


class TestBroadcastFillSignature(unittest.TestCase):
    """The trade arg was dropped from _broadcast_fill — verify it still works
    when called from _on_exec_details / _on_commission_report (which receive
    a trade from ib_async but no longer pass it on)."""

    def test_on_exec_details_broadcasts(self) -> None:
        client = _make_client()
        fill = _mock_fill(exec_id="LIVE-1")
        client._on_exec_details(MagicMock(), fill)  # trade arg ignored
        events = client.hub.replay(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "execDetailsEvent")
        self.assertEqual(events[0]["source"], "live")
        execution = cast(dict[str, Any], cast(dict[str, Any], events[0]["fill"])["execution"])
        self.assertEqual(execution["execId"], "LIVE-1")

    def test_on_commission_report_uses_passed_report(self) -> None:
        client = _make_client()
        fill = _mock_fill(exec_id="LIVE-2")
        report = MagicMock()
        report.execId = "LIVE-2"
        report.commission = 2.5
        report.currency = "USD"
        report.realizedPNL = 0.0
        report.yield_ = 0.0
        report.yieldRedemptionDate = 0
        client._on_commission_report(MagicMock(), fill, report)
        events = client.hub.replay(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "commissionReportEvent")
        self.assertEqual(events[0]["source"], "live")
        commission = cast(
            dict[str, Any], cast(dict[str, Any], events[0]["fill"])["commissionReport"]
        )
        self.assertEqual(commission["commission"], 2.5)


class TestExecIdDedup(unittest.IsolatedAsyncioTestCase):
    """Verify the cross-path execId set suppresses duplicate broadcasts."""

    def _commission_report_mock(self, exec_id: str) -> MagicMock:
        report = MagicMock()
        report.execId = exec_id
        report.commission = 1.0
        report.currency = "USD"
        report.realizedPNL = 0.0
        report.yield_ = 0.0
        report.yieldRedemptionDate = 0
        return report

    def test_live_commission_report_populates_set(self) -> None:
        client = _make_client()
        fill = _mock_fill(exec_id="LIVE-X")
        client._on_commission_report(
            MagicMock(), fill, self._commission_report_mock("LIVE-X"),
        )
        self.assertIn("LIVE-X", client._broadcast_exec_ids)

    def test_exec_details_does_not_populate_set(self) -> None:
        """Regression guard: only commissionReport should gate reconcile."""
        client = _make_client()
        fill = _mock_fill(exec_id="EXEC-ONLY")
        client._on_exec_details(MagicMock(), fill)
        self.assertNotIn("EXEC-ONLY", client._broadcast_exec_ids)

    async def test_reconcile_skips_already_seen_live(self) -> None:
        """If live commissionReport broadcast first, reconcile skips it."""
        fill = _mock_fill(exec_id="DUP-1")
        client = _make_client(req_executions_return=[fill])
        # Simulate the live path firing before reconcile.
        client._on_commission_report(
            MagicMock(), fill, self._commission_report_mock("DUP-1"),
        )
        live_count = len(client.hub.replay(0))
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            await client._reconcile_executions()
        # No new event broadcast; only the live one.
        self.assertEqual(len(client.hub.replay(0)), live_count)

    async def test_reconcile_broadcasts_then_skips_on_repeat(self) -> None:
        """First reconcile broadcasts; second sees execId in set, skips."""
        fill = _mock_fill(exec_id="RECON-1")
        client = _make_client(req_executions_return=[fill])
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            await client._reconcile_executions()
            await client._reconcile_executions()
        events = client.hub.replay(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "reconciled")

    async def test_reconcile_partial_dedup_mix(self) -> None:
        """Reconcile returns a mix of seen and unseen — only unseen go through."""
        seen = _mock_fill(exec_id="SEEN")
        new1 = _mock_fill(exec_id="NEW-1")
        new2 = _mock_fill(exec_id="NEW-2")
        client = _make_client(req_executions_return=[seen, new1, new2])
        # Pre-populate the set as if a live commissionReport already broadcast.
        client._on_commission_report(
            MagicMock(), seen, self._commission_report_mock("SEEN"),
        )
        live_count = len(client.hub.replay(0))
        with patch("client.RECONCILE_SETTLE_SECONDS", 0):
            await client._reconcile_executions()
        events = client.hub.replay(0)
        # 1 live + 2 reconciled (SEEN was suppressed).
        self.assertEqual(len(events) - live_count, 2)
        reconciled_exec_ids = {
            cast(dict[str, Any], cast(dict[str, Any], e["fill"])["execution"])["execId"]
            for e in events[live_count:]
        }
        self.assertEqual(reconciled_exec_ids, {"NEW-1", "NEW-2"})

    async def test_on_connected_preserves_exec_id_set(self) -> None:
        """Reconnect must not drop already-broadcast execIds — otherwise
        a transient same-day reconnect would re-emit every fill of the
        day as ``source="reconciled"``. The set persists for the
        lifetime of the bridge process.
        """
        client = _make_client()
        client._broadcast_exec_ids.update({"A", "B", "C"})
        # Patch call_later to a no-op so we don't schedule a real timer
        # against the test loop.
        with patch.object(asyncio.get_running_loop(), "call_later"):
            client._on_connected()
        self.assertEqual(client._broadcast_exec_ids, {"A", "B", "C"})


if __name__ == "__main__":
    unittest.main()
