"""Routes package — assembles middleware, handlers, and route table."""

from aiohttp import web

from bridge_routes.constants import AUTH_PREFIX, client_key
from bridge_routes.health import handle_health
from bridge_routes.middlewares import auth_middleware
from bridge_routes.order_place import handle_order
from bridge_routes.trades_list import handle_list_trades
from client import IBClient


def create_routes(client: IBClient) -> web.Application:
    """Create and return the aiohttp Application with all routes wired."""
    app = web.Application(middlewares=[auth_middleware])
    app[client_key] = client
    app.router.add_post(f"{AUTH_PREFIX}/order", handle_order)
    app.router.add_get(f"{AUTH_PREFIX}/trades", handle_list_trades)
    app.router.add_get("/health", handle_health)
    return app
