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
