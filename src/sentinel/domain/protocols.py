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


# ── Cycle 3: safe execution & disk cleanup protocols ─────────────────────────

from sentinel.domain.value_objects import ActionResult, AuditRecord  # noqa: E402


@runtime_checkable
class AppQuitter(Protocol):
    def quit(self, pid: int, name: str) -> bool: ...


@runtime_checkable
class ProcessSignaler(Protocol):
    def signal(self, pid: int, sig: int) -> bool: ...


@runtime_checkable
class AliveProbe(Protocol):
    def is_alive(self, pid: int) -> bool: ...


@runtime_checkable
class Killer(Protocol):
    def kill(self, candidate: ProcessCandidate) -> ActionResult: ...


@runtime_checkable
class ContainerStopper(Protocol):
    def stop(self, candidate: ContainerCandidate) -> ActionResult: ...


@runtime_checkable
class PathGuard(Protocol):
    def is_safe(self, path: str) -> bool: ...


@runtime_checkable
class ActivityGuard(Protocol):
    def is_active(self, project_dir: str) -> bool: ...


@runtime_checkable
class Trasher(Protocol):
    def trash(self, path: str) -> ActionResult: ...


@runtime_checkable
class Deleter(Protocol):
    def delete(self, path: str) -> ActionResult: ...


@runtime_checkable
class DiskCleaner(Protocol):
    def clean(self, state: SentinelState) -> tuple[ActionResult, ...]: ...


@runtime_checkable
class AuditLogger(Protocol):
    def record(self, record: AuditRecord) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    def notify(self, result: ActionResult) -> None: ...


# ── Cycle 4: config store, docker adapters, wake proxy, advisor, service ────

from typing import TYPE_CHECKING  # noqa: E402

from sentinel.domain.value_objects import (  # noqa: E402
    AdvisorRanking,
    DetectionResult,
    StackPorts,
    StatusReport,
    WakeOutcome,
    WakeRegistration,
)

if TYPE_CHECKING:
    from sentinel.config import AppConfig, SentinelPaths


@runtime_checkable
class ConfigStore(Protocol):
    def load(self) -> AppConfig: ...
    def save(self, config: AppConfig) -> None: ...
    def paths(self) -> SentinelPaths: ...


@runtime_checkable
class PortDiscoverer(Protocol):
    def discover(self, name: str) -> StackPorts: ...


@runtime_checkable
class StackRestarter(Protocol):
    def restart(self, registration: WakeRegistration) -> WakeOutcome: ...
    def is_running(self, stack: str) -> bool: ...


@runtime_checkable
class HealthGate(Protocol):
    async def wait_ready(self, port: int, timeout: float) -> bool: ...


@runtime_checkable
class WakeProxyManager(Protocol):
    def register(self, registration: WakeRegistration) -> None: ...
    def unregister(self, stack: str) -> None: ...
    def active(self) -> tuple[str, ...]: ...
    def stop_all(self) -> None: ...


@runtime_checkable
class Advisor(Protocol):
    def rank(self, detection: DetectionResult) -> AdvisorRanking: ...


@runtime_checkable
class ServiceController(Protocol):
    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> str: ...


@runtime_checkable
class StatusProvider(Protocol):
    def build(self) -> StatusReport: ...
