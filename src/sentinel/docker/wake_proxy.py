"""WakePortListener — per-port asyncio TCP listener for the container wake proxy.

On first connection the listener triggers the shared RestartOnceGate (exactly
one restart across all ports of a stack), awaits the HealthGate, then splices
bytes bidirectionally between the client and the live upstream container.

The listener stays bound on failure (restart or health-gate) so the next
client can trigger a fresh retry without losing the port.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sentinel.config import WakeProxyConfig
from sentinel.docker.restart_gate import RestartOnceGate
from sentinel.domain.protocols import HealthGate, StackRestarter, WakeProxyManager
from sentinel.domain.value_objects import PublishedPort, WakeOutcome

_log = logging.getLogger(__name__)


# ── byte-pump helpers ─────────────────────────────────────────────────────────


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


async def _timed_read(
    src: asyncio.StreamReader,
    buf: int,
    timeout: float | None,
) -> bytes | None:
    """Read from src; returns None on timeout so the caller can break cleanly."""
    if timeout is None:
        return await src.read(buf)
    try:
        return await asyncio.wait_for(src.read(buf), timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def _copy(
    src: asyncio.StreamReader,
    dst: asyncio.StreamWriter,
    buf: int,
    read_timeout: float | None = None,
) -> None:
    """Forward bytes from src to dst until EOF, error, or read_timeout; closes dst."""
    try:
        data = await _timed_read(src, buf, read_timeout)
        while data:
            dst.write(data)
            await dst.drain()
            data = await _timed_read(src, buf, read_timeout)
    except OSError:
        pass
    finally:
        await _close(dst)


# ── per-port listener ─────────────────────────────────────────────────────────


class WakePortListener:
    """Binds one asyncio TCP server on a PublishedPort and drives the wake sequence."""

    def __init__(
        self,
        port: PublishedPort,
        gate: RestartOnceGate,
        config: WakeProxyConfig,
        health_gate: HealthGate,
    ) -> None:
        self._port = port
        self._gate = gate
        self._config = config
        self._health_gate = health_gate
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection,
            self._config.bind_host,
            self._port.host_port,
            backlog=self._config.listen_backlog,
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if await self._wake(writer):
            await self._forward(reader, writer)

    async def _wake(self, writer: asyncio.StreamWriter) -> bool:
        outcome = await self._gate.wait()
        if outcome is WakeOutcome.RESTART_FAILED:
            await _close(writer)
            return False
        ready = await self._health_gate.wait_ready(
            self._port.container_port, self._config.health_timeout
        )
        if not ready:
            await _close(writer)
            return False
        return True

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            up_r, up_w = await asyncio.open_connection(
                self._config.bind_host, self._port.container_port
            )
        except OSError:
            _log.warning("upstream connect failed port=%d", self._port.container_port)
            await _close(writer)
            return
        await asyncio.gather(
            _copy(reader, up_w, self._config.connect_buffer),
            _copy(
                up_r, writer, self._config.connect_buffer, self._config.health_timeout
            ),
            return_exceptions=True,
        )


# ── composition root ──────────────────────────────────────────────────────────


@dataclass
class WakeComponents:
    """Dependency bundle: holds real adapters or injected fakes for testing."""

    restarter: StackRestarter
    health_gate: HealthGate


def build_wake_proxy(
    config: WakeProxyConfig,
    *,
    components: WakeComponents | None = None,
) -> WakeProxyManager:
    """Wire real adapters (or injected fakes) into an AsyncioWakeProxyManager."""
    if components is not None:
        return _from_components(config, components)
    return _from_os(config)


def _from_components(config: WakeProxyConfig, components: WakeComponents) -> WakeProxyManager:
    from sentinel.docker.wake_manager import AsyncioWakeProxyManager  # deferred

    return AsyncioWakeProxyManager(
        config=config,
        restarter=components.restarter,
        health_gate=components.health_gate,
    )


def _from_os(config: WakeProxyConfig) -> WakeProxyManager:
    from sentinel.docker.wake_manager import AsyncioWakeProxyManager  # deferred

    restarter, health_gate = _os_components(config)
    return AsyncioWakeProxyManager(
        config=config,
        restarter=restarter,
        health_gate=health_gate,
    )


def _os_components(config: WakeProxyConfig) -> tuple[StackRestarter, HealthGate]:
    """Construct real OS adapters; all heavy imports deferred to this body."""
    from sentinel.docker.stack_restarter import DockerStackRestarter  # noqa: PLC0415
    from sentinel.docker.wake_health import TcpHealthGate  # noqa: PLC0415

    return (
        DockerStackRestarter(),
        TcpHealthGate(
            host=config.bind_host,
            health_poll_interval=config.health_poll_interval,
        ),
    )
