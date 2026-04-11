"""Unit tests for bridge_routes/ws_events.py — WebSocket event handler."""

import os
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bridge_routes import create_routes
from client.event_hub import EventHub

_patcher = patch.dict(os.environ, {
    "API_TOKEN": "test-token",
    "WS_HEARTBEAT_INTERVAL": "5",
})


def setUpModule() -> None:
    _patcher.start()


def tearDownModule() -> None:
    _patcher.stop()


def _make_client() -> object:
    from unittest.mock import MagicMock, PropertyMock

    client = MagicMock()
    type(client).is_connected = PropertyMock(return_value=True)
    return client


class TestWsEventsConnect(AioHTTPTestCase):
    """WS endpoint accepts connections and streams events."""

    async def get_application(self) -> web.Application:
        self.hub = EventHub(buffer_size=100, max_subscribers=5)
        app = create_routes(_make_client(), self.hub)  # type: ignore[arg-type]
        return app

    async def test_connect_and_receive(self) -> None:
        async with self.client.ws_connect(
            "/ibkr/ws/events",
            headers={"Authorization": "Bearer test-token"},
        ) as ws:
            self.hub.broadcast({"type": "connected", "timestamp": "t1"})
            msg = await ws.receive_json()
            self.assertEqual(msg["type"], "connected")
            self.assertEqual(msg["seq"], 1)

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


class TestWsEventsAuth(AioHTTPTestCase):
    """WS endpoint requires authentication."""

    async def get_application(self) -> web.Application:
        hub = EventHub(buffer_size=10, max_subscribers=5)
        return create_routes(_make_client(), hub)  # type: ignore[arg-type]

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
        return create_routes(_make_client(), self.hub)  # type: ignore[arg-type]

    async def test_max_subscribers_rejects(self) -> None:
        async with self.client.ws_connect(
            "/ibkr/ws/events",
            headers={"Authorization": "Bearer test-token"},
        ):
            # Second connection should be closed with 4029
            async with self.client.ws_connect(
                "/ibkr/ws/events",
                headers={"Authorization": "Bearer test-token"},
            ) as ws2:
                await ws2.receive()
                self.assertTrue(ws2.closed)


if __name__ == "__main__":
    unittest.main()
