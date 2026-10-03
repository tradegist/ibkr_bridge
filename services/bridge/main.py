"""IBKR Bridge — entrypoint.

Starts the IB Gateway connection and HTTP API server, and shuts both
down cleanly on SIGTERM / SIGINT.
"""

import asyncio
import logging
import os
import signal

from aiohttp import web

from bridge_routes import create_routes
from client import IBClient, get_trading_mode
from client.event_hub import EventHub

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bridge")

# How long AppRunner.cleanup() waits for in-flight request handlers once
# the on_shutdown hook has closed the WS connections. Together with
# WS_SHUTDOWN_CLOSE_TIMEOUT this keeps shutdown under Docker's 10s
# SIGTERM → SIGKILL grace period.
HANDLER_SHUTDOWN_TIMEOUT = 3.0


def get_api_port() -> int:
    raw = os.environ.get("API_PORT", "5000").strip()
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid API_PORT={raw!r} — must be an integer"
        ) from None


async def run_ib(client: IBClient) -> None:
    """Connect to IB Gateway, then keep the connection alive until cancelled."""
    await client.connect()
    await client.watchdog()


async def amain() -> None:
    api_port = get_api_port()

    # Install the signal handlers before anything slow: the connect loop
    # can retry for minutes (e.g. while IB Gateway waits for 2FA) and a
    # stop request must still be honoured. With a handler installed,
    # SIGTERM also works when Python runs as PID 1.
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    hub = EventHub()
    client = IBClient(hub)

    log.info("IBKR Bridge starting (mode=%s)", get_trading_mode())

    # Start HTTP server first so /health is reachable while connecting
    app = create_routes(client, hub)
    runner = web.AppRunner(app, shutdown_timeout=HANDLER_SHUTDOWN_TIMEOUT)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", api_port)
    await site.start()
    log.info("HTTP API listening on port %d", api_port)

    # Subscribe before connecting so connectedEvent fires for the first
    # connect (arms the initial-sync gate via _on_connected).
    client.ib.disconnectedEvent += client.on_disconnect
    client.subscribe_events()

    ib_task = loop.create_task(run_ib(client))
    stop_task = loop.create_task(stop.wait())
    try:
        await asyncio.wait(
            {ib_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        if ib_task.done():
            # run_ib only ends by raising — surface the error.
            ib_task.result()
        log.info("Shutdown requested — stopping")
    finally:
        for task in (ib_task, stop_task):
            task.cancel()
        await asyncio.gather(ib_task, stop_task, return_exceptions=True)
        # Disconnect before stopping the HTTP server so the final
        # "disconnected" status event is queued to subscribers before
        # their connections close (best effort).
        client.shutdown()
        await runner.cleanup()
        log.info("Bridge stopped")


if __name__ == "__main__":
    asyncio.run(amain())
