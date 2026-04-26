"""E2E test fixtures — httpx client pointed at the local bridge test stack."""

from collections.abc import Iterator

import httpx
import pytest


class _MarketState:
    """Session-level flag: once any test detects market closed, all skip immediately."""

    def __init__(self) -> None:
        self._closed = False

    def mark_closed(self) -> None:
        self._closed = True

    def skip_if_closed(self) -> None:
        if self._closed:
            pytest.skip("Market is closed (detected earlier in session)")

BASE_URL = "http://localhost:15010"
API_TOKEN = "test-token"


@pytest.fixture(scope="session", autouse=True)
def _preflight_check() -> None:
    """Fail fast if bridge is unreachable."""
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5.0)
    except httpx.HTTPError:
        pytest.exit(
            f"bridge is not reachable at {BASE_URL}. "
            "Is the E2E stack running? (make e2e-up)",
            returncode=1,
        )
    if resp.status_code != 200:
        pytest.exit(
            f"bridge /health returned {resp.status_code}. "
            "Stack may not be ready yet.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def market_state() -> _MarketState:
    return _MarketState()


@pytest.fixture(scope="session")
def api() -> Iterator[httpx.Client]:
    """Shared httpx client with auth header, scoped to the entire test session."""
    with httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        timeout=15.0,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def anon_api() -> Iterator[httpx.Client]:
    """Httpx client without auth — for testing 401 responses."""
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
        yield client
