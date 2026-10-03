"""Unit tests for bridge_routes/ws_events.py — WebSocket event handler."""

import asyncio
import os
import unittest
from typing import cast
from unittest.mock import MagicMock, PropertyMock, patch

from aiohttp import WSCloseCode, WSMsgType, web
from aiohttp.test_utils import AioHTTPTestCase

from bridge_routes import create_routes
from bridge_routes.constants import ws_connections_key
from bridge_routes.ws_events import close_ws_connections, parse_last_seq, select_replay
from client import IBClient
from client.event_hub import EventHub

_patcher = patch.dict(os.environ, {
    "API_TOKEN": "test-token",
    "WS_HEARTBEAT_INTERVAL": "5",
})


def setUpModule() -> None:
    _patcher.start()


def tearDownModule() -> None:
    _patcher.stop()


def _make_client() -> IBClient:
    """MagicMock standing in for IBClient — only is_connected is needed."""
    client = MagicMock(spec=IBClient)
    type(client).is_connected = PropertyMock(return_value=True)
    return cast(IBClient, client)


async def _wait_for_connections(app: web.Application, count: int) -> None:
    """Wait until the server tracks *count* open WS connections.

    The handler registers a connection just after the upgrade completes,
    so a client that broadcasts straight after ``ws_connect`` can race it.
    """
    async with asyncio.timeout(2):
        while len(app[ws_connections_key]) != count:
            await asyncio.sleep(0.01)


class TestWsEventsConnect(AioHTTPTestCase):
    """WS endpoint accepts connections and streams events."""

    async def get_application(self) -> web.Application:
        self.hub = EventHub(buffer_size=100, max_subscribers=5)
        app = create_routes(_make_client(), self.hub)
        return app

    async def test_connect_and_receive(self) -> None:
        async with self.client.ws_connect(
            "/ibkr/ws/events",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            await _wait_for_connections(self.app, 1)
            self.hub.broadcast({"type": "connected", "timestamp": "t1"})
            msg = await ws.receive_json()
            self.assertEqual(msg["type"], "connected")
            self.assertEqual(msg["seq"], 1)
            self.assertEqual(msg["bridgeId"], self.hub.bridge_id)

    async def test_replay_on_connect(self) -> None:
        # Pre-fill buffer before client connects
        self.hub.broadcast({"type": "connected", "timestamp": "t1"})
        self.hub.broadcast({"type": "disconnected", "timestamp": "t2"})

        async with self.client.ws_connect(
            "/ibkr/ws/events?last_seq=0",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            msg1 = await ws.receive_json()
            msg2 = await ws.receive_json()
            self.assertEqual(msg1["seq"], 1)
            self.assertEqual(msg2["seq"], 2)

    async def test_replay_partial(self) -> None:
        self.hub.broadcast({"type": "a", "timestamp": "t1"})
        self.hub.broadcast({"type": "b", "timestamp": "t2"})
        self.hub.broadcast({"type": "c", "timestamp": "t3"})

        async with self.client.ws_connect(
            "/ibkr/ws/events?last_seq=2",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            msg = await ws.receive_json()
            self.assertEqual(msg["seq"], 3)
            self.assertEqual(msg["type"], "c")


class TestWsEventsReplayRules(AioHTTPTestCase):
    """Which buffered events a (re)connecting client gets replayed."""

    async def get_application(self) -> web.Application:
        self.hub = EventHub(buffer_size=100, max_subscribers=5)
        return create_routes(_make_client(), self.hub)

    def _prefill(self, count: int) -> None:
        for i in range(count):
            self.hub.broadcast({"type": "connected", "timestamp": f"t{i}"})

    async def _first_seq(self, query: str) -> int:
        """Connect with *query*, emit one live event, return the first seq received.

        The live event is broadcast only once the server has subscribed, so
        the first message is either a replayed event or that live one.
        """
        async with self.client.ws_connect(
            f"/ibkr/ws/events{query}",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            await _wait_for_connections(self.app, 1)
            self.hub.broadcast({"type": "connected", "timestamp": "live"})
            msg = await ws.receive_json()
            return int(msg["seq"])

    async def test_no_last_seq_skips_replay(self) -> None:
        # The July 18 incident: a client without last_seq used to get the
        # whole buffer. Now it only gets live events.
        self._prefill(3)
        self.assertEqual(await self._first_seq(""), 4)

    async def test_bridge_id_without_last_seq_skips_replay(self) -> None:
        self._prefill(3)
        self.assertEqual(await self._first_seq("?bridge_id=other"), 4)

    async def test_matching_bridge_id_replays_after_last_seq(self) -> None:
        self._prefill(3)
        query = f"?last_seq=2&bridge_id={self.hub.bridge_id}"
        self.assertEqual(await self._first_seq(query), 3)

    async def test_foreign_bridge_id_replays_whole_buffer(self) -> None:
        # Client's last_seq=240 came from a previous bridge process: this
        # process's seq restarted, so everything it buffered is new to them.
        self._prefill(3)
        self.assertEqual(await self._first_seq("?last_seq=240&bridge_id=old"), 1)

    async def test_empty_bridge_id_treated_as_absent(self) -> None:
        self._prefill(3)
        self.assertEqual(await self._first_seq("?last_seq=2&bridge_id="), 3)

    async def test_non_integer_last_seq_returns_400(self) -> None:
        resp = await self.client.get(
            "/ibkr/ws/events?last_seq=abc",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(resp.status, 400)
        self.assertIn("last_seq", (await resp.json())["error"])

    async def test_negative_last_seq_returns_400(self) -> None:
        resp = await self.client.get(
            "/ibkr/ws/events?last_seq=-1",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(resp.status, 400)
        self.assertIn("last_seq", (await resp.json())["error"])


class TestParseLastSeq(unittest.TestCase):
    def test_absent_is_none(self) -> None:
        self.assertIsNone(parse_last_seq(None))

    def test_valid_values(self) -> None:
        self.assertEqual(parse_last_seq("0"), 0)
        self.assertEqual(parse_last_seq("42"), 42)

    def test_invalid_values_raise(self) -> None:
        for raw in ("abc", "", "1.5", "-1"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_last_seq(raw)


class TestSelectReplay(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = EventHub(buffer_size=10, max_subscribers=2)
        for _ in range(3):
            self.hub.broadcast({"type": "connected"})

    def _seqs(self, last_seq: int | None, bridge_id: str | None) -> list[object]:
        return [e["seq"] for e in select_replay(self.hub, last_seq, bridge_id)]

    def test_rules(self) -> None:
        own = self.hub.bridge_id
        self.assertEqual(self._seqs(None, None), [])
        self.assertEqual(self._seqs(None, own), [])
        self.assertEqual(self._seqs(1, None), [2, 3])
        self.assertEqual(self._seqs(1, own), [2, 3])
        self.assertEqual(self._seqs(1, "other"), [1, 2, 3])
        self.assertEqual(self._seqs(999, own), [])


class TestWsEventsShutdown(AioHTTPTestCase):
    """The on_shutdown hook closes open connections with 1001."""

    async def get_application(self) -> web.Application:
        self.hub = EventHub(buffer_size=10, max_subscribers=5)
        return create_routes(_make_client(), self.hub)

    async def test_hook_is_registered(self) -> None:
        self.assertIn(close_ws_connections, self.app.on_shutdown)

    async def test_closes_open_connections_with_going_away(self) -> None:
        async with self.client.ws_connect(
            "/ibkr/ws/events",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            await _wait_for_connections(self.app, 1)
            await close_ws_connections(self.app)
            msg = await ws.receive()
            self.assertEqual(msg.type, WSMsgType.CLOSE)
            self.assertEqual(msg.data, WSCloseCode.GOING_AWAY)
        await _wait_for_connections(self.app, 0)
        self.assertEqual(self.hub.subscriber_count, 0)

    async def test_noop_without_connections(self) -> None:
        await close_ws_connections(self.app)  # must not raise


class TestWsEventsAuth(AioHTTPTestCase):
    """WS endpoint requires authentication."""

    async def get_application(self) -> web.Application:
        hub = EventHub(buffer_size=10, max_subscribers=5)
        return create_routes(_make_client(), hub)

    async def test_no_auth_returns_401(self) -> None:
        resp = await self.client.request("GET", "/ibkr/ws/events")
        self.assertEqual(resp.status, 401)

    async def test_wrong_token_returns_401(self) -> None:
        resp = await self.client.request(
            "GET", "/ibkr/ws/events",
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(resp.status, 401)


class TestWsEventsMaxSubscribers(AioHTTPTestCase):
    """WS endpoint rejects when at max subscribers."""

    async def get_application(self) -> web.Application:
        self.hub = EventHub(buffer_size=10, max_subscribers=1)
        return create_routes(_make_client(), self.hub)

    async def test_max_subscribers_rejects(self) -> None:
        async with (
            self.client.ws_connect(
                "/ibkr/ws/events",
                headers={"Authorization": "Bearer test-token"},
            ),
            self.client.ws_connect(
                "/ibkr/ws/events",
                headers={"Authorization": "Bearer test-token"},
            ) as ws2,
        ):
            # Second connection should be closed with 4029
            await ws2.receive()
            self.assertTrue(ws2.closed)


if __name__ == "__main__":
    unittest.main()
