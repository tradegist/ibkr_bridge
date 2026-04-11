"""Shared constants — importable without circular dependencies."""

from aiohttp import web

from client import IBClient
from client.event_hub import EventHub

client_key: web.AppKey[IBClient] = web.AppKey("client", IBClient)
hub_key: web.AppKey[EventHub] = web.AppKey("hub", EventHub)
ws_heartbeat_key: web.AppKey[int] = web.AppKey("ws_heartbeat", int)

# Path prefix guarded by auth middleware. Route registration and middleware
# must both reference this so they stay in sync.
AUTH_PREFIX = "/ibkr"
