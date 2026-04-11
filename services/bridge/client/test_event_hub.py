"""Unit tests for client/event_hub.py."""

import asyncio
import os
import unittest

from client.event_hub import EventHub, get_ws_buffer_size, get_ws_max_subscribers

# ── Env var getters ──────────────────────────────────────────────────

_ORIG_ENV: dict[str, str | None] = {}
_TEST_ENV = {
    "WS_BUFFER_SIZE": "500",
    "WS_MAX_SUBSCRIBERS": "10",
}


def setUpModule() -> None:
    for key, val in _TEST_ENV.items():
        _ORIG_ENV[key] = os.environ.get(key)
        os.environ[key] = val


def tearDownModule() -> None:
    for key, orig in _ORIG_ENV.items():
        if orig is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig


class TestGetWsBufferSize(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(get_ws_buffer_size(), 500)

    def test_invalid_raises(self) -> None:
        os.environ["WS_BUFFER_SIZE"] = "abc"
        with self.assertRaises(SystemExit):
            get_ws_buffer_size()
        os.environ["WS_BUFFER_SIZE"] = _TEST_ENV["WS_BUFFER_SIZE"]

    def test_zero_raises(self) -> None:
        os.environ["WS_BUFFER_SIZE"] = "0"
        with self.assertRaises(SystemExit):
            get_ws_buffer_size()
        os.environ["WS_BUFFER_SIZE"] = _TEST_ENV["WS_BUFFER_SIZE"]


class TestGetWsMaxSubscribers(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(get_ws_max_subscribers(), 10)

    def test_invalid_raises(self) -> None:
        os.environ["WS_MAX_SUBSCRIBERS"] = "abc"
        with self.assertRaises(SystemExit):
            get_ws_max_subscribers()
        os.environ["WS_MAX_SUBSCRIBERS"] = _TEST_ENV["WS_MAX_SUBSCRIBERS"]


# ── EventHub ─────────────────────────────────────────────────────────


class TestEventHubBroadcast(unittest.TestCase):
    def test_assigns_monotonic_seq(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "connected"})
        hub.broadcast({"type": "disconnected"})
        self.assertEqual(hub.seq, 2)

    def test_events_stored_in_buffer(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "connected"})
        hub.broadcast({"type": "disconnected"})
        self.assertEqual(len(hub.replay(0)), 2)

    def test_buffer_evicts_oldest(self) -> None:
        hub = EventHub(buffer_size=3, max_subscribers=2)
        for i in range(5):
            hub.broadcast({"type": "connected", "i": i})
        events = hub.replay(0)
        self.assertEqual(len(events), 3)
        # Oldest two (i=0, i=1) evicted; remaining are i=2,3,4
        self.assertEqual(events[0]["i"], 2)
        self.assertEqual(events[2]["i"], 4)

    def test_broadcast_reaches_subscribers(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        q1 = hub.subscribe("sub-1")
        q2 = hub.subscribe("sub-2")
        hub.broadcast({"type": "connected"})
        self.assertFalse(q1.empty())
        self.assertFalse(q2.empty())
        ev1 = q1.get_nowait()
        ev2 = q2.get_nowait()
        self.assertEqual(ev1["seq"], 1)
        self.assertEqual(ev2["seq"], 1)

    def test_broadcast_without_subscribers_still_buffers(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "connected"})
        self.assertEqual(len(hub.replay(0)), 1)


class TestEventHubSubscribe(unittest.TestCase):
    def test_subscribe_returns_queue(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        q = hub.subscribe("sub-1")
        self.assertIsInstance(q, asyncio.Queue)

    def test_duplicate_id_raises(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.subscribe("sub-1")
        with self.assertRaises(RuntimeError, msg="already registered"):
            hub.subscribe("sub-1")

    def test_max_subscribers_enforced(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.subscribe("sub-1")
        hub.subscribe("sub-2")
        with self.assertRaises(RuntimeError, msg="Max subscribers"):
            hub.subscribe("sub-3")

    def test_subscriber_count(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=5)
        self.assertEqual(hub.subscriber_count, 0)
        hub.subscribe("a")
        self.assertEqual(hub.subscriber_count, 1)
        hub.subscribe("b")
        self.assertEqual(hub.subscriber_count, 2)


class TestEventHubUnsubscribe(unittest.TestCase):
    def test_unsubscribe_removes(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.subscribe("sub-1")
        hub.unsubscribe("sub-1")
        self.assertEqual(hub.subscriber_count, 0)

    def test_unsubscribe_unknown_is_noop(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.unsubscribe("nonexistent")  # Should not raise

    def test_unsubscribe_frees_slot(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=1)
        hub.subscribe("sub-1")
        hub.unsubscribe("sub-1")
        hub.subscribe("sub-2")  # Should not raise
        self.assertEqual(hub.subscriber_count, 1)


class TestEventHubReplay(unittest.TestCase):
    def test_replay_from_zero_returns_all(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "a"})
        hub.broadcast({"type": "b"})
        hub.broadcast({"type": "c"})
        events = hub.replay(0)
        self.assertEqual(len(events), 3)

    def test_replay_from_seq_returns_newer(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "a"})
        hub.broadcast({"type": "b"})
        hub.broadcast({"type": "c"})
        events = hub.replay(2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["seq"], 3)

    def test_replay_from_current_seq_returns_empty(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "a"})
        events = hub.replay(1)
        self.assertEqual(len(events), 0)

    def test_replay_from_future_seq_returns_empty(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        hub.broadcast({"type": "a"})
        events = hub.replay(999)
        self.assertEqual(len(events), 0)


class TestEventHubQueueFull(unittest.TestCase):
    def test_full_queue_drops_event(self) -> None:
        hub = EventHub(buffer_size=10, max_subscribers=2)
        q = hub.subscribe("sub-1")
        # Fill the queue (maxsize=1000)
        for i in range(1000):
            hub.broadcast({"type": "fill", "i": i})
        # Next broadcast should drop for this subscriber but not raise
        hub.broadcast({"type": "fill", "i": 1000})
        self.assertEqual(q.qsize(), 1000)  # Still at max, didn't grow
