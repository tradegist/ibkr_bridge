"""Smoke tests — verify the bridge stack is up and auth is enforced."""

import httpx


def test_health_ok(api: httpx.Client) -> None:
    resp = api.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["tradingMode"] in ("paper", "live")


def test_order_requires_auth(anon_api: httpx.Client) -> None:
    resp = anon_api.post("/ibkr/order")
    assert resp.status_code == 401


def test_trades_requires_auth(anon_api: httpx.Client) -> None:
    resp = anon_api.get("/ibkr/trades")
    assert resp.status_code == 401
