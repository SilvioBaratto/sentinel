from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class PressureLevel(IntEnum):
    """Kernel memory pressure level from sysctl kern.memorystatus_vm_pressure_level.

    Kernel emits 1, 2, or 4 only — 3 is never emitted. [3-0]
    """

    NORMAL = 1
    WARN = 2
    CRITICAL = 4


class SentinelState(Enum):
    """Canonical state emitted by the state machine; gates all downstream actions."""

    NORMAL = "normal"
    WARN = "warn"
    CRITICAL = "critical"
    DISK_LOW = "disk_low"


@dataclass(frozen=True)
class DiskUsage:
    mount: str
    free_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class SwapUsage:
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class MemoryReport:
    """psutil macOS memory snapshot — reporting/history ONLY, never a state gate. [3-0]"""

    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class ResourceSample:
    timestamp: float
    pressure: PressureLevel
    swap: SwapUsage
    disks: tuple[DiskUsage, ...]
    memory: MemoryReport


@dataclass(frozen=True)
class CandidateSignal:
    """A proposed state transition — a candidate, never an action."""

    proposed_state: SentinelState
    reason: str
    triggering_sample: ResourceSample


# ── Cycle 2: process & container detection vocabulary ────────────────────────


class ProcessProtection(Enum):
    """Safe-default classification for a process — PROTECTED wins on any ambiguity."""

    PROTECTED = "protected"
    REAPABLE = "reapable"


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    cmdline: tuple[str, ...]
    has_tty: bool
    tty: str | None
    pgid: int | None
    cpu_percent: float
    rss_bytes: int
    create_time: float | None
    idle_seconds: float = 0.0


@dataclass(frozen=True)
class ProcessClassification:
    pid: int
    name: str
    protection: ProcessProtection
    reason: str


@dataclass(frozen=True)
class ProcessCandidate:
    info: ProcessInfo
    idle_seconds: float
    cpu_percent: float
    reason: str


@dataclass(frozen=True)
class FrontmostApp:
    bundle_id: str | None
    name: str | None
    pid: int | None


@dataclass(frozen=True)
class ContainerStats:
    container_id: str
    name: str
    cpu_percent: float
    net_rx_bytes: int
    net_tx_bytes: int
    block_read_bytes: int
    block_write_bytes: int


@dataclass(frozen=True)
class ContainerCandidate:
    name: str
    container_id: str
    idle_seconds: float
    cpu_percent: float
    reason: str


@dataclass(frozen=True)
class DetectionResult:
    processes: tuple[ProcessCandidate, ...]
    containers: tuple[ContainerCandidate, ...]


# ── Cycle 3: safe execution & disk cleanup vocabulary ────────────────────────


class Reversibility(Enum):
    REVERSIBLE = "reversible"
    PERMANENT = "permanent"

    def __bool__(self) -> bool:
        return self is Reversibility.REVERSIBLE


class ActionKind(Enum):
    KILL_PROCESS = "kill_process"
    STOP_CONTAINER = "stop_container"
    TRASH = "trash"
    DELETE = "delete"


class ExecutionMode(Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DRY_RUN = "dry_run"


class KillStage(Enum):
    QUIT = "quit"
    SIGTERM = "sigterm"
    SIGKILL = "sigkill"
    NONE = "none"


class KillOutcome(Enum):
    EXITED = "exited"
    SURVIVED = "survived"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True)
class ActionResult:
    kind: ActionKind
    target: str
    success: bool
    reversibility: Reversibility
    bytes_freed: int = 0
    detail: str = ""
    dry_run: bool = False
    outcome: KillOutcome | None = None
    stage: KillStage | None = None


@dataclass(frozen=True)
class AuditRecord:
    timestamp: float
    kind: ActionKind
    target: str
    success: bool
    reversibility: Reversibility
    bytes_freed: int
    mode: ExecutionMode
    detail: str


# Alias: tests and downstream code may use either name.
MachineState = SentinelState


# ── Cycle 4: wake proxy, service, advisor, status vocabulary ────────────────

from typing import Mapping  # noqa: E402


@dataclass(frozen=True)
class PublishedPort:
    host_ip: str
    host_port: int
    container_port: int
    protocol: str = "tcp"


@dataclass(frozen=True)
class StackPorts:
    stack: str
    containers: tuple[str, ...]
    ports: tuple[PublishedPort, ...]
    compose_project: str | None = None


@dataclass(frozen=True)
class WakeRegistration:
    stack: str
    ports: tuple[PublishedPort, ...]
    restart_command: tuple[str, ...]


class WakeOutcome(Enum):
    RESTARTED = "restarted"
    ALREADY_RUNNING = "already_running"
    RESTART_FAILED = "restart_failed"
    HEALTH_TIMEOUT = "health_timeout"


@dataclass(frozen=True)
class AdvisorRanking:
    ordered_targets: tuple[str, ...]
    explanations: Mapping[str, str]


@dataclass(frozen=True)
class StatusReport:
    pressure: PressureLevel
    state: SentinelState
    memory: MemoryReport
    swap: SwapUsage
    disks: tuple[DiskUsage, ...]
    recent_actions: tuple[ActionResult, ...]
    idle_processes: tuple[ProcessCandidate, ...]
    idle_containers: tuple[ContainerCandidate, ...]
    wake_proxies: tuple[str, ...]
