"""
Composition root for the Cycle 1 Sentinel monitoring pipeline.

``build_pipeline`` is the single place all real adapters (or injected fakes)
are constructed and wired together.  Everything downstream depends only on
Protocols.  There is no timed loop or scheduler here — the timed loop is
Cycle 4.  ``step()`` performs one synchronous pass through the chain.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from sentinel.config import MonitorConfig
from sentinel.domain.protocols import History, ResourceSampler, StateMachine, ThresholdEngine
from sentinel.domain.value_objects import DiskUsage, SentinelState
from sentinel.monitor.clock import SystemClock
from sentinel.monitor.rolling_history import RollingHistory
from sentinel.monitor.sampler import DefaultResourceSampler
from sentinel.rules.state_machine import SentinelStateMachine
from sentinel.rules.thresholds import DefaultThresholdEngine


class _DiskAdapter:
    """Adapt a no-arg disk reader to the DiskReader protocol (mount param ignored).

    Test fakes expose ``read() -> DiskUsage``; the DefaultResourceSampler expects
    ``read(mount: str) -> DiskUsage``.  This adapter bridges the two.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def read(self, mount: str) -> DiskUsage:  # noqa: ARG002
        return self._inner.read()


class MonitoringPipeline:
    """Thin orchestrator: one call to step() runs one pass through the sensing chain."""

    def __init__(
        self,
        sampler: ResourceSampler,
        history: History,
        evaluator: ThresholdEngine,
        state_machine: StateMachine,
    ) -> None:
        self._sampler = sampler
        self._history = history
        self._evaluator = evaluator
        self._state_machine = state_machine

    def step(self) -> SentinelState:
        sample = self._sampler.sample()
        self._history.append(sample)
        signal = self._evaluator.evaluate(self._history)
        return self._state_machine.transition(signal)


# ── private wiring helpers ────────────────────────────────────────────────────


def _fake_sampler(readers: dict, clock: SystemClock) -> DefaultResourceSampler:
    """Build a sampler wired to injected fake readers."""
    return DefaultResourceSampler(
        clock=clock,
        pressure_reader=readers["pressure"],
        swap_reader=readers["swap"],
        memory_reader=readers["memory"],
        disk_reader=_DiskAdapter(readers["disk"]),
        mounts=("/",),
    )


def _os_readers() -> tuple:
    """Deferred imports so top-level import of pipeline never spawns a subprocess."""
    from sentinel.monitor.disk import PsutilDiskReader
    from sentinel.monitor.memory import PsutilMemoryReader
    from sentinel.monitor.pressure import SysctlPressureReader
    from sentinel.monitor.swap import SysctlSwapReader

    return (
        SysctlPressureReader(),
        SysctlSwapReader(),
        PsutilMemoryReader(),
        PsutilDiskReader(),
    )


def _real_sampler(clock: SystemClock) -> DefaultResourceSampler:
    p, sw, m, d = _os_readers()
    return DefaultResourceSampler(clock, p, sw, m, d, ("/",))


def build_pipeline(
    config: MonitorConfig,
    *,
    clock: Callable[[], float] | None = None,
    readers: dict | None = None,
) -> MonitoringPipeline:
    """Composition root: the only place real adapters (or injected fakes) are named together."""
    clock_fn = clock if clock is not None else time.monotonic
    sampler_clock = SystemClock(monotonic=clock_fn)
    sampler = _fake_sampler(readers, sampler_clock) if readers else _real_sampler(sampler_clock)
    history = RollingHistory(config)
    engine = DefaultThresholdEngine(config, clock_fn)
    return MonitoringPipeline(sampler, history, engine, SentinelStateMachine())
