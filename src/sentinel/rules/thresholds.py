from __future__ import annotations

from typing import Callable

from sentinel.config import MonitorConfig
from sentinel.domain.protocols import History
from sentinel.domain.value_objects import (
    CandidateSignal,
    PressureLevel,
    ResourceSample,
    SentinelState,
)
from sentinel.rules.hysteresis import HysteresisGate

_SEVERITY: dict[SentinelState, int] = {
    SentinelState.NORMAL: 0,
    SentinelState.DISK_LOW: 1,
    SentinelState.WARN: 2,
    SentinelState.CRITICAL: 3,
}


def _raw_state(sample: ResourceSample, cfg: MonitorConfig) -> SentinelState:
    if sample.pressure == PressureLevel.CRITICAL:
        return SentinelState.CRITICAL
    if sample.pressure == PressureLevel.WARN:
        return SentinelState.WARN
    if any(d.free_bytes < cfg.disk_low_floor for d in sample.disks):
        return SentinelState.DISK_LOW
    return SentinelState.NORMAL


def _reason(state: SentinelState, sample: ResourceSample) -> str:
    mem_gib = sample.memory.used_bytes >> 30
    if state == SentinelState.CRITICAL:
        return (
            f"pressure CRITICAL (level={int(sample.pressure)}); mem ~{mem_gib} GiB used"
        )
    if state == SentinelState.WARN:
        return f"pressure WARN (level={int(sample.pressure)}); mem ~{mem_gib} GiB used"
    if state == SentinelState.DISK_LOW:
        low = min(sample.disks, key=lambda d: d.free_bytes)
        return f"disk {low.mount}: {low.free_bytes >> 30} GiB free (below floor)"
    return "all resources within normal bounds"


class DefaultThresholdEngine:
    """Stateful threshold engine: feeds each latest sample through directional gates.

    Escalation gate has no cooldown (fast-to-escalate).
    Clear gate carries config.cooldown (slow-to-de-escalate).
    """

    def __init__(self, config: MonitorConfig, clock: Callable[[], float]) -> None:
        self._cfg = config
        self._current = SentinelState.NORMAL
        self._escalate = HysteresisGate(config.confirm_samples, 0.0, clock)
        self._clear = HysteresisGate(
            config.confirm_samples_clear, config.cooldown, clock
        )

    def evaluate(self, history: History) -> CandidateSignal:
        sample = history.latest()
        if sample is None:
            return CandidateSignal(SentinelState.NORMAL, "no samples yet", _DUMMY)
        raw = _raw_state(sample, self._cfg)
        self._current = self._next_state(raw, sample.timestamp)
        return CandidateSignal(self._current, _reason(self._current, sample), sample)

    def _next_state(self, raw: SentinelState, now: float) -> SentinelState:
        diff = _SEVERITY[raw] - _SEVERITY[self._current]
        if diff > 0:
            self._clear.confirmed(False, now)
            return raw if self._escalate.confirmed(True, now) else self._current
        if diff < 0:
            self._escalate.confirmed(False, now)
            return raw if self._clear.confirmed(True, now) else self._current
        self._escalate.confirmed(False, now)
        self._clear.confirmed(False, now)
        return self._current


# Sentinel value used only when history is empty (should never reach production).
from sentinel.domain.value_objects import DiskUsage, MemoryReport, SwapUsage  # noqa: E402

_DUMMY = ResourceSample(
    timestamp=0.0,
    pressure=PressureLevel.NORMAL,
    swap=SwapUsage(0, 0, 0),
    disks=(DiskUsage("/", 0, 0),),
    memory=MemoryReport(0, 0, 0),
)
