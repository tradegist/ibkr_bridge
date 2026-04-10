"""GET /health — connection status."""

from aiohttp import web

from bridge_models import HealthResponse
from bridge_routes.constants import client_key
from client import IBClient, get_trading_mode


async def handle_health(request: web.Request) -> web.Response:
    client: IBClient = request.app[client_key]
    resp = HealthResponse(
        connected=client.is_connected,
        tradingMode=get_trading_mode(),
    )
    return web.json_response(resp.model_dump())
