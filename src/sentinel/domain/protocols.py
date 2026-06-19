from __future__ import annotations

from typing import Protocol, runtime_checkable

from sentinel.domain.value_objects import (
    CandidateSignal,
    DiskUsage,
    MemoryReport,
    PressureLevel,
    ResourceSample,
    SentinelState,
    SwapUsage,
)


@runtime_checkable
class PressureReader(Protocol):
    def read(self) -> PressureLevel: ...


@runtime_checkable
class SwapReader(Protocol):
    def read(self) -> SwapUsage: ...


@runtime_checkable
class DiskReader(Protocol):
    def read(self, mount: str) -> DiskUsage: ...


@runtime_checkable
class MemoryReader(Protocol):
    """psutil macOS memory — reporting/history ONLY, never a state gate. [3-0]"""

    def read(self) -> MemoryReport: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...


@runtime_checkable
class ResourceSampler(Protocol):
    def sample(self) -> ResourceSample: ...


@runtime_checkable
class History(Protocol):
    def append(self, sample: ResourceSample) -> None: ...

    def recent(self, n: int) -> tuple[ResourceSample, ...]: ...

    def latest(self) -> ResourceSample | None: ...

    def __len__(self) -> int: ...


@runtime_checkable
class ThresholdEngine(Protocol):
    def evaluate(self, history: History) -> CandidateSignal: ...


@runtime_checkable
class StateMachine(Protocol):
    @property
    def state(self) -> SentinelState: ...

    def transition(self, signal: CandidateSignal) -> SentinelState: ...
