"""Routes package — assembles middleware, handlers, and route table."""

from aiohttp import web

from bridge_routes.constants import AUTH_PREFIX, client_key, hub_key, ws_heartbeat_key
from bridge_routes.health import handle_health
from bridge_routes.middlewares import auth_middleware
from bridge_routes.order_place import handle_order
from bridge_routes.trades_list import handle_list_trades
from bridge_routes.ws_events import get_ws_heartbeat, handle_ws_events
from client import IBClient
from client.event_hub import EventHub


def create_routes(client: IBClient, hub: EventHub) -> web.Application:
    """Create and return the aiohttp Application with all routes wired."""
    app = web.Application(middlewares=[auth_middleware])
    app[client_key] = client
    app[hub_key] = hub
    app[ws_heartbeat_key] = get_ws_heartbeat()
    app.router.add_post(f"{AUTH_PREFIX}/order", handle_order)
    app.router.add_get(f"{AUTH_PREFIX}/trades", handle_list_trades)
    app.router.add_get(f"{AUTH_PREFIX}/ws/events", handle_ws_events)
    app.router.add_get("/health", handle_health)
    return app
