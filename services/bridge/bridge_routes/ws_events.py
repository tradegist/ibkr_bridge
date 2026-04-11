"""GET /ibkr/ws/events — WebSocket event stream."""

import logging
import uuid

from aiohttp import web

from bridge_routes.constants import hub_key

log = logging.getLogger("routes")


def _get_ws_heartbeat() -> int:
    """Return heartbeat interval for WS connections (seconds)."""
    import os

    raw = os.environ.get("WS_HEARTBEAT_INTERVAL", "30").strip()
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid WS_HEARTBEAT_INTERVAL={raw!r} — must be an integer"
        ) from None
    if value < 1:
        raise SystemExit(
            f"Invalid WS_HEARTBEAT_INTERVAL={value} — must be >= 1"
        )
    return value


async def handle_ws_events(request: web.Request) -> web.WebSocketResponse:
    """Upgrade to WebSocket and stream ib_async events to the client.

    Query params:
        last_seq — replay buffered events with seq > last_seq (default 0)
    """
    hub = request.app[hub_key]
    subscriber_id = uuid.uuid4().hex

    ws = web.WebSocketResponse(heartbeat=_get_ws_heartbeat())
    await ws.prepare(request)

    try:
        queue = hub.subscribe(subscriber_id)
    except RuntimeError as exc:
        log.warning("WS rejected: %s", exc)
        await ws.close(code=4029, message=str(exc).encode())
        return ws

    try:
        # Replay buffered events if client requests it
        raw_last_seq = request.query.get("last_seq", "0")
        try:
            last_seq = int(raw_last_seq)
        except ValueError:
            last_seq = 0

        for event in hub.replay(last_seq):
            await ws.send_json(event)

        # Stream new events until client disconnects
        while not ws.closed:
            event = await queue.get()
            if ws.closed:
                break
            await ws.send_json(event)
    finally:
        hub.unsubscribe(subscriber_id)

    return ws
