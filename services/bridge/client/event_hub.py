"""EventHub — pub/sub hub for broadcasting ib_async events to WS subscribers."""

import asyncio
import logging
import os
from collections import deque

log = logging.getLogger("event-hub")


def get_ws_buffer_size() -> int:
    raw = os.environ.get("WS_BUFFER_SIZE", "500").strip()
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid WS_BUFFER_SIZE={raw!r} — must be an integer"
        ) from None
    if value < 1:
        raise SystemExit(
            f"Invalid WS_BUFFER_SIZE={value} — must be >= 1"
        )
    return value


def get_ws_max_subscribers() -> int:
    raw = os.environ.get("WS_MAX_SUBSCRIBERS", "10").strip()
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid WS_MAX_SUBSCRIBERS={raw!r} — must be an integer"
        ) from None
    if value < 1:
        raise SystemExit(
            f"Invalid WS_MAX_SUBSCRIBERS={value} — must be >= 1"
        )
    return value


class EventHub:
    """Broadcast events to WebSocket subscribers with a replay buffer.

    Events are assigned a monotonically increasing sequence number and
    stored in a fixed-size ring buffer.  Subscribers receive events via
    an ``asyncio.Queue``.  On reconnect a client can request replay of
    buffered events starting from a given sequence number.
    """

    def __init__(
        self,
        buffer_size: int | None = None,
        max_subscribers: int | None = None,
    ) -> None:
        self._buffer_size = buffer_size if buffer_size is not None else get_ws_buffer_size()
        self._max_subscribers = max_subscribers if max_subscribers is not None else get_ws_max_subscribers()
        self._buffer: deque[dict[str, object]] = deque(maxlen=self._buffer_size)
        self._seq: int = 0
        self._subscribers: dict[str, asyncio.Queue[dict[str, object]]] = {}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def seq(self) -> int:
        return self._seq

    def broadcast(self, event: dict[str, object]) -> None:
        """Assign a sequence number and push to buffer + all subscribers."""
        self._seq += 1
        event["seq"] = self._seq
        self._buffer.append(event)
        for sub_id, queue in self._subscribers.items():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    "Subscriber %s queue full — dropping event seq=%d",
                    sub_id, self._seq,
                )

    def subscribe(self, subscriber_id: str) -> asyncio.Queue[dict[str, object]]:
        """Register a subscriber and return its event queue.

        Raises ``RuntimeError`` if subscriber limit reached or ID already taken.
        """
        if subscriber_id in self._subscribers:
            raise RuntimeError(
                f"Subscriber {subscriber_id!r} already registered"
            )
        if len(self._subscribers) >= self._max_subscribers:
            raise RuntimeError(
                f"Max subscribers ({self._max_subscribers}) reached"
            )
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1000)
        self._subscribers[subscriber_id] = queue
        log.info(
            "Subscriber %s connected (%d/%d)",
            subscriber_id, len(self._subscribers), self._max_subscribers,
        )
        return queue

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber.  No-op if not registered."""
        removed = self._subscribers.pop(subscriber_id, None)
        if removed is not None:
            log.info(
                "Subscriber %s disconnected (%d/%d)",
                subscriber_id, len(self._subscribers), self._max_subscribers,
            )

    def replay(self, from_seq: int) -> list[dict[str, object]]:
        """Return buffered events with seq > *from_seq*."""
        result: list[dict[str, object]] = []
        for ev in self._buffer:
            seq = ev.get("seq")
            if isinstance(seq, int) and seq > from_seq:
                result.append(ev)
        return result
