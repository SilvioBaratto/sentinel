"""AsyncioWakeProxyManager — synchronous WakeProxyManager facade.

Owns a dedicated asyncio event loop on a daemon thread.  All public methods
are synchronous and thread-safe; they marshal work onto the loop via
asyncio.run_coroutine_threadsafe so the rest of Sentinel never touches asyncio.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

from sentinel.config import WakeProxyConfig
from sentinel.docker.restart_gate import RestartOnceGate
from sentinel.docker.wake_proxy import WakePortListener
from sentinel.domain.protocols import HealthGate, StackRestarter
from sentinel.domain.value_objects import WakeOutcome, WakeRegistration


class AsyncioWakeProxyManager:
    """Synchronous facade; the asyncio loop is fully confined to this module."""

    def __init__(
        self,
        config: WakeProxyConfig,
        restarter: StackRestarter,
        health_gate: HealthGate,
    ) -> None:
        self._config = config
        self._restarter = restarter
        self._health_gate = health_gate
        self._stacks: dict[str, list[WakePortListener]] = {}
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    # ── public synchronous API ────────────────────────────────────────────────

    def register(self, registration: WakeRegistration) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._async_register(registration), self._loop
        )
        future.result(timeout=10.0)

    def unregister(self, stack: str) -> None:
        if not self._loop.is_running():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._async_unregister(stack), self._loop
        )
        future.result(timeout=10.0)

    def active(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._stacks)

    def stop_all(self) -> None:
        if not self._loop.is_running():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_stop_all(), self._loop
            )
            future.result(timeout=15.0)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)

    # ── internal async helpers ────────────────────────────────────────────────

    def _make_restarter(self, reg: WakeRegistration) -> Callable[[], Awaitable[None]]:
        restarter = self._restarter
        loop = self._loop

        async def _restart() -> None:
            outcome = await loop.run_in_executor(None, restarter.restart, reg)
            if outcome is WakeOutcome.RESTART_FAILED:
                raise RuntimeError(f"restart failed: {reg.stack!r}")

        return _restart

    async def _async_register(self, reg: WakeRegistration) -> None:
        gate = RestartOnceGate(restarter=self._make_restarter(reg))
        listeners = [
            WakePortListener(p, gate, self._config, self._health_gate)
            for p in reg.ports
        ]
        for listener in listeners:
            await listener.start()
        with self._lock:
            self._stacks[reg.stack] = listeners

    async def _async_unregister(self, stack: str) -> None:
        with self._lock:
            listeners = self._stacks.pop(stack, [])
        for listener in listeners:
            await listener.stop()

    async def _async_stop_all(self) -> None:
        with self._lock:
            stacks = dict(self._stacks)
            self._stacks.clear()
        for listeners in stacks.values():
            for listener in listeners:
                await listener.stop()


# Alias: tests and downstream code import WakeProxyManager from this module.
WakeProxyManager = AsyncioWakeProxyManager
