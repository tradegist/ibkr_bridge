"""Unit tests for main.py — graceful shutdown on SIGTERM."""

import asyncio
import os
import signal
import unittest
from unittest.mock import MagicMock, patch

import main
from client import IBClient


class TestGracefulShutdown(unittest.IsolatedAsyncioTestCase):
    """amain() must stop cleanly on SIGTERM instead of waiting for SIGKILL."""

    async def asyncSetUp(self) -> None:
        self.ib_started = asyncio.Event()
        self.ib_cancelled = False
        self.client = MagicMock(spec=IBClient)
        self.client.ib = MagicMock()
        # Port 0: bind an ephemeral port, no clash with a running bridge.
        self.enterContext(patch.dict(os.environ, {"API_PORT": "0"}))
        self.enterContext(patch.object(main, "IBClient", return_value=self.client))
        self.enterContext(patch.object(main, "run_ib", new=self._fake_run_ib))

    async def _fake_run_ib(self, client: IBClient) -> None:
        self.ib_started.set()
        try:
            await asyncio.Event().wait()  # connected forever
        except asyncio.CancelledError:
            self.ib_cancelled = True
            raise

    async def _start(self) -> "asyncio.Task[None]":
        task = asyncio.get_running_loop().create_task(main.amain())
        # run_ib starts after the signal handlers are installed.
        async with asyncio.timeout(5):
            await self.ib_started.wait()
        return task

    async def test_sigterm_stops_bridge(self) -> None:
        task = await self._start()
        os.kill(os.getpid(), signal.SIGTERM)
        async with asyncio.timeout(5):
            await task
        self.assertTrue(self.ib_cancelled)
        self.client.shutdown.assert_called_once()

    async def test_ib_task_failure_still_shuts_down(self) -> None:
        async def failing_run_ib(client: IBClient) -> None:
            raise RuntimeError("boom")

        with (
            patch.object(main, "run_ib", new=failing_run_ib),
            self.assertRaisesRegex(RuntimeError, "boom"),
        ):
            async with asyncio.timeout(5):
                await main.amain()
        self.client.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
