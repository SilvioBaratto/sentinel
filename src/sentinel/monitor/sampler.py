from __future__ import annotations

from sentinel.domain.protocols import (
    Clock,
    DiskReader,
    MemoryReader,
    PressureReader,
    SwapReader,
)
from sentinel.domain.value_objects import ResourceSample


class DefaultResourceSampler:
    """Compose one read from each platform shim into a timestamped ResourceSample.

    Strict SRP: no thresholding, no state — composition only.
    Exceptions from readers propagate unchanged (fail-safe: caller holds last state).
    """

    def __init__(
        self,
        clock: Clock,
        pressure_reader: PressureReader,
        swap_reader: SwapReader,
        memory_reader: MemoryReader,
        disk_reader: DiskReader,
        mounts: tuple[str, ...],
    ) -> None:
        self._clock = clock
        self._pressure_reader = pressure_reader
        self._swap_reader = swap_reader
        self._memory_reader = memory_reader
        self._disk_reader = disk_reader
        self._mounts = mounts

    def sample(self) -> ResourceSample:
        return ResourceSample(
            timestamp=self._clock.now(),
            pressure=self._pressure_reader.read(),
            swap=self._swap_reader.read(),
            memory=self._memory_reader.read(),
            disks=tuple(self._disk_reader.read(m) for m in self._mounts),
        )
