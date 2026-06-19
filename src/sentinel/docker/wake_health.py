"""TcpHealthGate — polls asyncio.open_connection until a port accepts connections."""

from __future__ import annotations

import asyncio


class TcpHealthGate:
    """Probes host:port every health_poll_interval; True on first connect, False on timeout."""

    def __init__(self, host: str, health_poll_interval: float) -> None:
        self._host = host
        self._interval = health_poll_interval

    async def wait_ready(self, port: int, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._probe(port):
                return True
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(self._interval, remaining))
        return False

    async def _probe(self, port: int) -> bool:
        try:
            _, writer = await asyncio.open_connection(self._host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            return False
