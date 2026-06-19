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
