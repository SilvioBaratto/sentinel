"""RestartOnceGate — coordinates a container restart across concurrent port-wake triggers.

On success the resolved Future is kept so later waves short-circuit (no re-restart).
On failure the slot is cleared so the next connection can retry.
Both transitions are atomic under the same asyncio.Lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sentinel.domain.value_objects import WakeOutcome


class RestartOnceGate:
    """Invokes restarter exactly once even when N coroutines race to wake the same stack."""

    def __init__(self, restarter: Callable[[], Awaitable[None]]) -> None:
        self._restarter = restarter
        self._lock = asyncio.Lock()
        self._future: asyncio.Future[WakeOutcome] | None = None

    async def wait(self) -> WakeOutcome:
        future = await self._acquire_future()
        outcome = await asyncio.shield(future)
        if outcome is WakeOutcome.RESTART_FAILED:
            await self._clear_if_same(future)
        return outcome

    async def _acquire_future(self) -> asyncio.Future[WakeOutcome]:
        async with self._lock:
            if self._future is None:
                self._future = asyncio.get_running_loop().create_future()
                asyncio.create_task(self._run(self._future))
            return self._future

    async def _clear_if_same(self, future: asyncio.Future[WakeOutcome]) -> None:
        async with self._lock:
            if self._future is future:
                self._future = None

    async def _run(self, future: asyncio.Future[WakeOutcome]) -> None:
        try:
            await self._restarter()
            future.set_result(WakeOutcome.RESTARTED)
        except Exception:
            future.set_result(WakeOutcome.RESTART_FAILED)
