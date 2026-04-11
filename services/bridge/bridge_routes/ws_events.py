"""GET /ibkr/ws/events — WebSocket event stream."""

import asyncio
import logging
import os
import uuid

from aiohttp import web

from bridge_routes.constants import hub_key, ws_heartbeat_key

log = logging.getLogger("routes")


def get_ws_heartbeat() -> int:
    """Parse and validate WS_HEARTBEAT_INTERVAL from the environment.

    Called once at startup from ``create_routes``.
    """
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

    ws = web.WebSocketResponse(heartbeat=request.app[ws_heartbeat_key])
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

        # Stream new events until client disconnects.
        # We race queue.get() against ws.receive() so that a client
        # disconnect during a quiet period is detected immediately
        # instead of blocking forever on queue.get().
        queue_task: asyncio.Task[dict[str, object]] | None = None
        ws_task: asyncio.Task[web.WSMessage] | None = None
        try:
            while not ws.closed:
                if queue_task is None:
                    queue_task = asyncio.ensure_future(queue.get())
                if ws_task is None:
                    ws_task = asyncio.ensure_future(ws.receive())

                done, _ = await asyncio.wait(
                    {queue_task, ws_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if ws_task in done:
                    # Client sent a message or disconnected
                    msg = ws_task.result()
                    ws_task = None
                    if msg.type in (
                        web.WSMsgType.CLOSE,
                        web.WSMsgType.CLOSING,
                        web.WSMsgType.CLOSED,
                        web.WSMsgType.ERROR,
                    ):
                        break

                if queue_task in done:
                    event = queue_task.result()
                    queue_task = None
                    if not ws.closed:
                        await ws.send_json(event)
        finally:
            # Cancel any in-flight tasks to avoid dangling coroutines
            if queue_task is not None:
                queue_task.cancel()
            if ws_task is not None:
                ws_task.cancel()
    finally:
        hub.unsubscribe(subscriber_id)

    return ws
