from __future__ import annotations

from typing import Protocol, runtime_checkable

from sentinel.domain.value_objects import (
    CandidateSignal,
    ContainerCandidate,
    ContainerStats,
    DiskUsage,
    FrontmostApp,
    MemoryReport,
    PressureLevel,
    ProcessCandidate,
    ProcessClassification,
    ProcessInfo,
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


# ── Cycle 2: process & container detection protocols ─────────────────────────

from typing import Mapping  # noqa: E402


@runtime_checkable
class ProcessLister(Protocol):
    def list(self) -> tuple[ProcessInfo, ...]: ...


@runtime_checkable
class FrontmostAppReader(Protocol):
    def read(self) -> FrontmostApp: ...


@runtime_checkable
class HidIdleReader(Protocol):
    def read(self) -> float: ...


@runtime_checkable
class ProcessClassifier(Protocol):
    def classify(
        self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]
    ) -> ProcessClassification: ...


@runtime_checkable
class ProcessIdleDetector(Protocol):
    def detect(self, state: SentinelState) -> tuple[ProcessCandidate, ...]: ...


@runtime_checkable
class ContainerStatsReader(Protocol):
    def read(self) -> tuple[ContainerStats, ...]: ...


@runtime_checkable
class ContainerSessionReader(Protocol):
    def active_session_names(self) -> frozenset[str]: ...


@runtime_checkable
class ContainerStatsProvider(Protocol):
    """Duck-typed reader interface consumed by DefaultContainerIdleDetector.

    Distinct from ContainerStatsReader (returns ContainerStats objects) and
    ContainerSessionReader (returns active session names); this provider
    returns a pre-combined dict per container that the idle gate consumes
    directly.  Satisfied by _DockerLiveReader in sentinel.detection and by
    FakeStatsReader in tests.
    """

    def list_containers(self) -> list[str]: ...

    def get_stats(self, name: str) -> dict: ...


@runtime_checkable
class ContainerIdleDetector(Protocol):
    def detect(self, state: SentinelState) -> tuple[ContainerCandidate, ...]: ...
