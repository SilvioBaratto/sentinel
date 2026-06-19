"""Shared fixtures and builders for all Cycle 1 tests.

The make_sample() builder is the single seam that makes every component
testable on Apple Silicon + Intel without touching the real OS.
"""

from __future__ import annotations

import pytest

from sentinel.domain.value_objects import (
    CandidateSignal,
    DiskUsage,
    MemoryReport,
    PressureLevel,
    ResourceSample,
    SentinelState,
    SwapUsage,
)


def make_disk(
    mount: str = "/",
    free_gib: float = 50.0,
    total_gib: float = 200.0,
) -> DiskUsage:
    _GiB = 1024**3
    return DiskUsage(
        mount=mount,
        free_bytes=int(free_gib * _GiB),
        total_bytes=int(total_gib * _GiB),
    )


def make_swap(total_bytes: int = 0, used_bytes: int = 0) -> SwapUsage:
    return SwapUsage(
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        free_bytes=total_bytes - used_bytes,
    )


def make_memory(total_gib: float = 16.0, used_gib: float = 8.0) -> MemoryReport:
    _GiB = 1024**3
    total = int(total_gib * _GiB)
    used = int(used_gib * _GiB)
    return MemoryReport(total_bytes=total, used_bytes=used, free_bytes=total - used)


def make_sample(
    timestamp: float = 0.0,
    pressure: PressureLevel = PressureLevel.NORMAL,
    swap: SwapUsage | None = None,
    disks: tuple[DiskUsage, ...] | None = None,
    memory: MemoryReport | None = None,
) -> ResourceSample:
    return ResourceSample(
        timestamp=timestamp,
        pressure=pressure,
        swap=swap or make_swap(),
        disks=disks or (make_disk(),),
        memory=memory or make_memory(),
    )


def make_signal(
    proposed_state: SentinelState = SentinelState.NORMAL,
    reason: str = "test",
    sample: ResourceSample | None = None,
) -> CandidateSignal:
    return CandidateSignal(
        proposed_state=proposed_state,
        reason=reason,
        triggering_sample=sample or make_sample(),
    )


@pytest.fixture
def normal_sample() -> ResourceSample:
    return make_sample(pressure=PressureLevel.NORMAL)


@pytest.fixture
def warn_sample() -> ResourceSample:
    return make_sample(pressure=PressureLevel.WARN)


@pytest.fixture
def critical_sample() -> ResourceSample:
    return make_sample(pressure=PressureLevel.CRITICAL)
