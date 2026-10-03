"""Unit tests for IBClient connection lifecycle: disconnect + shutdown."""

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from ib_async import IB

from client import IBClient
from client.event_hub import EventHub


def _make_client() -> IBClient:
    hub = EventHub(buffer_size=100, max_subscribers=5)
    client = IBClient(hub)
    client.ib = MagicMock(spec=IB)
    return client


def _ib(client: IBClient) -> Any:
    """Return the (mocked) ib attribute, untyped, for stubbing in tests."""
    return client.ib


class TestOnDisconnect(unittest.IsolatedAsyncioTestCase):
    async def test_schedules_reconnect(self) -> None:
        client = _make_client()
        with patch.object(client, "_reconnect", new=AsyncMock()) as reconnect:
            client.on_disconnect()
            self.assertEqual(len(client._background_tasks), 1)
            await asyncio.gather(*client._background_tasks)
        reconnect.assert_awaited_once()

    async def test_broadcasts_disconnected_status(self) -> None:
        client = _make_client()
        with patch.object(client, "_reconnect", new=AsyncMock()):
            client.on_disconnect()
            await asyncio.gather(*client._background_tasks)
        event = client.hub.replay(0)[-1]
        self.assertEqual(event["type"], "disconnected")
        self.assertEqual(event["bridgeId"], client.hub.bridge_id)


class TestShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_disconnects_without_scheduling_reconnect(self) -> None:
        client = _make_client()
        # ib_async emits disconnectedEvent synchronously from disconnect().
        _ib(client).disconnect.side_effect = client.on_disconnect
        with patch.object(client, "_reconnect", new=AsyncMock()) as reconnect:
            client.shutdown()
        _ib(client).disconnect.assert_called_once()
        self.assertEqual(client._background_tasks, set())
        reconnect.assert_not_called()
        # Subscribers still learn the IB connection is gone.
        self.assertEqual(client.hub.replay(0)[-1]["type"], "disconnected")

    async def test_cancels_background_tasks(self) -> None:
        client = _make_client()
        pending = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        client._background_tasks.add(pending)
        client.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)

    async def test_safe_when_never_connected(self) -> None:
        # IB.disconnect() is a no-op (no event) when not connected.
        client = _make_client()
        client.shutdown()
        _ib(client).disconnect.assert_called_once()
        self.assertTrue(client._shutting_down)


if __name__ == "__main__":
    unittest.main()
