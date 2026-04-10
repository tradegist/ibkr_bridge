"""Shared constants — importable without circular dependencies."""

from aiohttp import web

from client import IBClient

client_key: web.AppKey[IBClient] = web.AppKey("client", IBClient)

# Path prefix guarded by auth middleware. Route registration and middleware
# must both reference this so they stay in sync.
AUTH_PREFIX = "/ibkr"
