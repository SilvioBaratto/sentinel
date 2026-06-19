from __future__ import annotations

from collections import deque

from sentinel.domain.value_objects import ResourceSample


class RollingHistory:
    """Bounded FIFO buffer of ResourceSamples — pure storage, no trend math (SRP).

    Backed by collections.deque(maxlen=config.history_size) so the bound is
    enforced by construction: oldest entry is silently evicted when full.
    """

    def __init__(self, config) -> None:
        self._buf: deque[ResourceSample] = deque(maxlen=config.history_size)

    def append(self, sample: ResourceSample) -> None:
        self._buf.append(sample)

    def recent(self, n: int) -> tuple[ResourceSample, ...]:
        if n <= 0:
            return ()
        return tuple(self._buf)[-n:]

    def latest(self) -> ResourceSample | None:
        return self._buf[-1] if self._buf else None

    def __len__(self) -> int:
        return len(self._buf)
