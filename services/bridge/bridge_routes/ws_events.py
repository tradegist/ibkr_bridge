"""GET /ibkr/ws/events — WebSocket event stream."""

import asyncio
import contextlib
import logging
import os
import uuid

from aiohttp import WSCloseCode, WSMessage, WSMsgType, web

from bridge_routes.constants import hub_key, ws_connections_key, ws_heartbeat_key
from client.event_hub import EventHub

log = logging.getLogger("routes")

# Upper bound for closing all WS connections on shutdown (see
# close_ws_connections). Docker sends SIGKILL 10s after SIGTERM.
WS_SHUTDOWN_CLOSE_TIMEOUT = 5.0


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


def parse_last_seq(raw: str | None) -> int | None:
    """Parse the ``last_seq`` query param — ``None`` when the client sent none.

    Raises ``ValueError`` for a non-integer or negative value. The handler
    rejects those with HTTP 400 instead of guessing: falling back to 0
    would replay the whole buffer to a client that asked for something else.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"Invalid last_seq={raw!r} — must be a non-negative integer"
        ) from None
    if value < 0:
        raise ValueError(
            f"Invalid last_seq={value} — must be a non-negative integer"
        )
    return value


def select_replay(
    hub: EventHub, last_seq: int | None, bridge_id: str | None,
) -> list[dict[str, object]]:
    """Return the buffered events a (re)connecting client has not seen.

    - No ``last_seq``: nothing — the client only wants live events.
    - ``bridge_id`` differs from this process's: the bridge restarted
      since the client's last session, so ``last_seq`` counts events of
      a buffer that no longer exists. Replay everything this process has
      buffered (it only holds events emitted since this process started).
    - Same ``bridge_id``, or none sent (older clients): events with
      ``seq > last_seq``.
    """
    if last_seq is None:
        return []
    if bridge_id is not None and bridge_id != hub.bridge_id:
        log.info(
            "WS client resumed from a previous bridge process (last_seq=%d) "
            "— replaying this process's buffer from the start",
            last_seq,
        )
        return hub.replay(0)
    return hub.replay(last_seq)


async def close_ws_connections(app: web.Application) -> None:
    """``on_shutdown`` hook: close every open WS with 1001 (Going Away).

    Without it, ``AppRunner.cleanup()`` waits for the streaming handlers,
    which never return on their own, until its shutdown timeout. Closes
    run concurrently and are bounded by ``WS_SHUTDOWN_CLOSE_TIMEOUT`` so
    shutdown stays inside Docker's 10s stop grace period.
    """
    # Snapshot: each handler discards its own entry as its close completes.
    open_connections = app[ws_connections_key]
    connections = list(open_connections)
    if not connections:
        return
    log.info("Closing %d WS connection(s) for shutdown", len(connections))
    try:
        async with asyncio.timeout(WS_SHUTDOWN_CLOSE_TIMEOUT):
            results = await asyncio.gather(
                *(
                    ws.close(code=WSCloseCode.GOING_AWAY, message=b"Bridge shutting down")
                    for ws in connections
                ),
                return_exceptions=True,
            )
    except TimeoutError:
        log.warning(
            "WS clients did not acknowledge close within %.0fs — "
            "dropping the connections",
            WS_SHUTDOWN_CLOSE_TIMEOUT,
        )
        return
    for result in results:
        if isinstance(result, BaseException):
            log.error("Failed to close WS connection on shutdown: %r", result)


async def handle_ws_events(request: web.Request) -> web.StreamResponse:
    """Upgrade to WebSocket and stream ib_async events to the client.

    Query params (see ``select_replay`` for the replay rules):
        last_seq  — highest ``seq`` the client has processed. Omit it for
                    live events only. Non-integer / negative → HTTP 400.
        bridge_id — ``bridgeId`` of the events ``last_seq`` came from.
    """
    hub = request.app[hub_key]
    try:
        last_seq = parse_last_seq(request.query.get("last_seq"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    # An empty ``bridge_id=`` carries no information — treat it as absent.
    bridge_id = request.query.get("bridge_id") or None

    subscriber_id = uuid.uuid4().hex
    ws = web.WebSocketResponse(heartbeat=request.app[ws_heartbeat_key])
    await ws.prepare(request)

    try:
        queue = hub.subscribe(subscriber_id)
    except RuntimeError as exc:
        log.warning("WS rejected: %s", exc)
        await ws.close(code=4029, message=str(exc).encode())
        return ws

    connections = request.app[ws_connections_key]
    connections.add(ws)
    try:
        for event in select_replay(hub, last_seq, bridge_id):
            await ws.send_json(event)

        # Stream new events until client disconnects.
        # We race queue.get() against ws.receive() so that a client
        # disconnect during a quiet period is detected immediately
        # instead of blocking forever on queue.get().
        queue_task: asyncio.Task[dict[str, object]] | None = None
        ws_task: asyncio.Task[WSMessage] | None = None
        # The handler runs as a coroutine; the loop is always available.
        loop = asyncio.get_running_loop()
        try:
            while not ws.closed:
                if queue_task is None:
                    queue_task = loop.create_task(queue.get())
                if ws_task is None:
                    ws_task = loop.create_task(ws.receive())

                done, _ = await asyncio.wait(
                    {queue_task, ws_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if ws_task in done:
                    # Client sent a message or disconnected
                    msg = ws_task.result()
                    ws_task = None
                    if msg.type in (
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSING,
                        WSMsgType.CLOSED,
                        WSMsgType.ERROR,
                    ):
                        break

                if queue_task in done:
                    event = queue_task.result()
                    queue_task = None
                    if not ws.closed:
                        await ws.send_json(event)
        finally:
            # Cancel any in-flight tasks and drain them to avoid
            # "Task was destroyed but it is pending" warnings.
            if queue_task is not None:
                queue_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await queue_task
            if ws_task is not None:
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await ws_task
    finally:
        connections.discard(ws)
        hub.unsubscribe(subscriber_id)

    return ws
