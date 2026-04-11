"""IBKR Bridge — entrypoint.

Starts the IB Gateway connection and HTTP API server.
"""

import asyncio
import logging
import os

from aiohttp import web

from bridge_routes import create_routes
from client import IBClient, get_trading_mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bridge")


def get_api_port() -> int:
    raw = os.environ.get("API_PORT", "5000").strip()
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(
            f"Invalid API_PORT={raw!r} — must be an integer"
        ) from None


async def amain() -> None:
    api_port = get_api_port()

    client = IBClient()

    log.info("IBKR Bridge starting (mode=%s)", get_trading_mode())

    # Start HTTP server first so /health is reachable while connecting
    app = create_routes(client)
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", api_port)
    await site.start()
    log.info("HTTP API listening on port %d", api_port)

    await client.connect()

    client.ib.disconnectedEvent += client.on_disconnect

    await client.watchdog()


if __name__ == "__main__":
    asyncio.run(amain())
