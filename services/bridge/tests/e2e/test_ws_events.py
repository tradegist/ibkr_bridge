"""E2E tests for the WebSocket event publisher (/ibkr/ws/events).

Tests verify:
- WS connection with auth
- WS rejects unauthenticated connections
- Real-time event delivery after placing an order
- Replay via ?last_seq= parameter
"""

import asyncio
from typing import Any

import aiohttp
import httpx
import pytest

BASE_URL = "http://localhost:15010"
WS_URL = "ws://localhost:15010/ibkr/ws/events"
API_TOKEN = "test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def _place_market_order(api: httpx.Client) -> dict[str, Any]:
    """Place a small market BUY order and return the response body."""
    resp = api.post("/ibkr/order", json={
        "contract": {
            "symbol": "AAPL",
            "secType": "STK",
            "exchange": "SMART",
            "currency": "USD",
        },
        "order": {
            "action": "BUY",
            "totalQuantity": 1,
            "orderType": "MKT",
        },
    })
    assert resp.status_code == 200, f"Order failed: {resp.text}"
    body: dict[str, Any] = resp.json()
    return body


class TestWsAuth:
    """WebSocket authentication tests."""

    def test_ws_rejects_no_auth(self) -> None:
        """WS upgrade without auth header should be rejected."""
        async def _run() -> None:
            async with aiohttp.ClientSession() as session, session.ws_connect(WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    return
                pytest.fail("Expected WS to be closed by server")

        # Auth middleware should reject with 401 before upgrade completes.
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc_info:
            asyncio.run(_run())

        assert exc_info.value.status == 401

    def test_ws_connects_with_auth(self) -> None:
        """WS upgrade with valid auth should succeed."""
        async def _run() -> bool:
            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(WS_URL) as ws:
                assert not ws.closed
                await ws.close()
                return True

        result = asyncio.run(_run())
        assert result is True


class TestWsEventDelivery:
    """Verify that placing an order produces WebSocket events."""

    def test_order_produces_exec_event(self, api: httpx.Client) -> None:
        """Subscribe to WS, place an order, receive at least one exec event.

        Skips when no execution events arrive (market closed / weekends).
        """
        async def _run() -> list[dict[str, Any]]:
            events: list[dict[str, Any]] = []
            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(WS_URL) as ws:
                _place_market_order(api)

                # Collect events for up to 30s (fills can take a moment)
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=30)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            events.append(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break
                        if any(e.get("type") == "execDetailsEvent" for e in events):
                            break
                except TimeoutError:
                    pass

                await ws.close()
            return events

        events = asyncio.run(_run())

        exec_events = [e for e in events if e.get("type") == "execDetailsEvent"]
        if not exec_events:
            pytest.skip(
                "No execDetailsEvent received within timeout — "
                "market is likely closed (weekend/holiday/after-hours)"
            )

        # Validate envelope structure
        event = exec_events[0]
        assert "seq" in event
        assert isinstance(event["seq"], int)
        assert event["seq"] > 0
        assert "timestamp" in event
        assert "fill" in event

        # Validate fill structure
        fill: dict[str, Any] = event["fill"]
        assert "contract" in fill
        assert "execution" in fill
        contract: dict[str, Any] = fill["contract"]
        execution: dict[str, Any] = fill["execution"]
        assert contract["symbol"] == "AAPL"
        assert execution["side"] in ("BOT", "SLD")
        assert execution["shares"] > 0

    def test_replay_returns_buffered_events(self, api: httpx.Client) -> None:
        """After placing an order, reconnecting with ?last_seq=0 should replay."""
        async def _run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            first_events: list[dict[str, Any]] = []

            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(WS_URL) as ws:
                _place_market_order(api)

                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=30)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            first_events.append(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break
                        if any(e.get("type") == "execDetailsEvent" for e in first_events):
                            break
                except TimeoutError:
                    pass
                await ws.close()

            if not any(e.get("type") == "execDetailsEvent" for e in first_events):
                pytest.skip(
                    "No execDetailsEvent received — "
                    "market is likely closed; replay test not meaningful"
                )

            # Reconnect with last_seq=0 to replay all buffered events
            replay_events: list[dict[str, Any]] = []
            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(f"{WS_URL}?last_seq=0") as ws:
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            replay_events.append(msg.json())
                        else:
                            break
                except TimeoutError:
                    pass
                await ws.close()

            return first_events, replay_events

        first_events, replay_events = asyncio.run(_run())

        assert len(replay_events) >= len(first_events), (
            f"Replay returned {len(replay_events)} events, "
            f"expected at least {len(first_events)}"
        )

        # Replay must include at least one execution event
        replay_exec = [e for e in replay_events if e.get("type") == "execDetailsEvent"]
        assert len(replay_exec) > 0, "Replay did not include execDetailsEvent"

        # Sequence numbers should be monotonically increasing
        seqs = [int(e["seq"]) for e in replay_events]
        assert seqs == sorted(seqs), f"Replay seqs not sorted: {seqs}"

    def test_replay_skips_already_seen(self, api: httpx.Client) -> None:
        """Reconnecting with last_seq=N should only return events with seq > N."""
        async def _run() -> tuple[int, list[dict[str, Any]]]:
            # Step 1: get current max seq by connecting briefly
            current_max_seq = 0
            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(f"{WS_URL}?last_seq=0") as ws:
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=3)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            current_max_seq = max(current_max_seq, data.get("seq", 0))
                        else:
                            break
                except TimeoutError:
                    pass
                await ws.close()

            if current_max_seq == 0:
                pytest.skip("No events in buffer — nothing to test replay against")

            # Step 2: place a new order to generate fresh events
            new_max_seq = current_max_seq
            async with aiohttp.ClientSession(headers=AUTH_HEADERS) as session, session.ws_connect(WS_URL) as ws:
                _place_market_order(api)
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=30)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            new_max_seq = max(new_max_seq, data.get("seq", 0))
                            if data.get("type") == "execDetailsEvent":
                                break
                        else:
                            break
                except TimeoutError:
                    pass
                await ws.close()

            if new_max_seq <= current_max_seq:
                pytest.skip(
                    "No new events after placing order — "
                    "market is likely closed"
                )

            # Step 3: reconnect with last_seq = previous max
            replay_events: list[dict[str, Any]] = []
            async with (
                aiohttp.ClientSession(headers=AUTH_HEADERS) as session,
                session.ws_connect(f"{WS_URL}?last_seq={current_max_seq}") as ws,
            ):
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            replay_events.append(msg.json())
                        else:
                            break
                except TimeoutError:
                    pass
                await ws.close()

            return current_max_seq, replay_events

        prev_seq, new_events = asyncio.run(_run())

        assert len(new_events) > 0, (
            f"Replay with last_seq={prev_seq} returned no events"
        )

        # All replayed events should have seq > the previous max
        for event in new_events:
            assert int(event["seq"]) > prev_seq, (
                f"Replay returned event seq={event['seq']} "
                f"which is <= last_seq={prev_seq}"
            )
