"""
Source-blind tests for Issue #9: build_pipeline wires DefaultThresholdEngine.

Authored against acceptance criteria only — no implementation source was read.
All behavioral assertions are derived from the criteria and requirements.md.

Criteria coverage:
  AC1 — build_pipeline wires DefaultThresholdEngine; _PipelineEvaluator removed.
  AC2 — (structural) pipeline.py is glue only; verified implicitly by AC1/AC3/AC4.
  AC3 — End-to-end integration walk asserts against DefaultThresholdEngine's behavior.
  AC4 — Regression: directional cooldown and asymmetric clear survive composition.
  AC5 — _EMPTY (pipeline.py:33) and _DUMMY (thresholds.py:82) de-duplicated.

Skipped (oracle: NOT VERIFIABLE):
  - "SOLID, clean code; methods < 10 lines, classes < 50 lines" (subjective metrics)
  - "No raw-state/hysteresis/precedence logic in pipeline.py" — structural property;
    the behavioral tests for AC1/AC3/AC4 collectively prove the pipeline delegates
    all logic to DefaultThresholdEngine and does not duplicate it.
"""

from __future__ import annotations

import importlib

from hypothesis import given, settings
from hypothesis import strategies as st

from sentinel.config import MonitorConfig
from sentinel.domain.value_objects import (
    DiskUsage,
    MemoryReport,
    PressureLevel,
    SentinelState,
    SwapUsage,
)
from sentinel.pipeline import MonitoringPipeline, build_pipeline

# ─── constants ────────────────────────────────────────────────────────────────

_GiB = 1024**3
_DISK_FLOOR_GiB = 20.0
_DISK_FLOOR_BYTES = int(_DISK_FLOOR_GiB * _GiB)


# ─── FakeClock ────────────────────────────────────────────────────────────────


class FakeClock:
    """Zero-argument callable returning a controllable float timestamp (seconds).

    Injected into build_pipeline so DefaultThresholdEngine's cooldown logic is
    exercised without relying on wall-clock time.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# ─── local domain builders ────────────────────────────────────────────────────


def _make_disk(
    mount: str = "/",
    free_gib: float = 100.0,
    total_gib: float = 500.0,
) -> DiskUsage:
    return DiskUsage(
        mount=mount,
        free_bytes=int(free_gib * _GiB),
        total_bytes=int(total_gib * _GiB),
    )


def _make_swap(total_bytes: int = 0, used_bytes: int = 0) -> SwapUsage:
    return SwapUsage(
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        free_bytes=total_bytes - used_bytes,
    )


def _make_memory(total_gib: float = 16.0, used_gib: float = 8.0) -> MemoryReport:
    total = int(total_gib * _GiB)
    used = int(used_gib * _GiB)
    return MemoryReport(total_bytes=total, used_bytes=used, free_bytes=total - used)


# ─── scripted / constant fake readers ────────────────────────────────────────


class _ScriptedPressureReader:
    def __init__(self, sequence: list[PressureLevel]) -> None:
        self._iter = iter(sequence)

    def read(self) -> PressureLevel:
        return next(self._iter)


class _ConstantPressureReader:
    def __init__(self, level: PressureLevel) -> None:
        self._level = level

    def read(self) -> PressureLevel:
        return self._level


class _ScriptedDiskReader:
    def __init__(self, sequence: list[DiskUsage]) -> None:
        self._iter = iter(sequence)

    def read(self) -> DiskUsage:
        return next(self._iter)


class _ConstantDiskReader:
    def __init__(self, disk: DiskUsage) -> None:
        self._disk = disk

    def read(self) -> DiskUsage:
        return self._disk


class _ConstantMemoryReader:
    def __init__(self, report: MemoryReport) -> None:
        self._report = report

    def read(self) -> MemoryReport:
        return self._report


class _ConstantSwapReader:
    def __init__(self, swap: SwapUsage) -> None:
        self._swap = swap

    def read(self) -> SwapUsage:
        return self._swap


# ─── MonitorConfig factory ────────────────────────────────────────────────────


def _cfg(
    confirm: int = 2,
    confirm_clear: int = 4,
    cooldown: float = 0.0,
    disk_floor_gib: float = _DISK_FLOOR_GiB,
) -> MonitorConfig:
    return MonitorConfig(
        confirm_samples=confirm,
        confirm_samples_clear=confirm_clear,
        cooldown=cooldown,
        disk_low_floor=int(disk_floor_gib * _GiB),
    )


# ─── scripted pipeline builder ────────────────────────────────────────────────


def _scripted_pipeline(
    pressure_seq: list[PressureLevel],
    config: MonitorConfig,
    disk_seq: list[DiskUsage] | None = None,
    clock: FakeClock | None = None,
) -> MonitoringPipeline:
    """Build a pipeline whose fake readers deliver pre-programmed values per step()."""
    n = len(pressure_seq)
    if disk_seq is None:
        disk_seq = [_make_disk(free_gib=100.0)] * n
    return build_pipeline(
        config,
        clock=clock,
        readers={
            "pressure": _ScriptedPressureReader(pressure_seq),
            "disk": _ScriptedDiskReader(disk_seq),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — build_pipeline constructs and wires DefaultThresholdEngine;
#        _PipelineEvaluator is removed entirely.
# ═════════════════════════════════════════════════════════════════════════════


def test_when_pipeline_module_inspected_then_pipeline_evaluator_is_absent():
    """_PipelineEvaluator must not exist as an attribute of sentinel.pipeline (AC1).

    Assumption: after the refactor, the name '_PipelineEvaluator' is wholly absent
    from the module namespace — not merely unused or unreachable.
    """
    m = importlib.import_module("sentinel.pipeline")
    assert not hasattr(m, "_PipelineEvaluator"), (
        "_PipelineEvaluator must be removed entirely from sentinel.pipeline"
    )


def test_when_build_pipeline_called_with_monitor_config_and_clock_then_pipeline_returned():
    """build_pipeline(MonitorConfig, clock=..., readers=...) returns MonitoringPipeline (AC1).

    Assumption: the refactored build_pipeline signature accepts MonitorConfig as the
    first positional argument and a clock= kwarg for injecting into DefaultThresholdEngine.
    """
    clock = FakeClock()
    config = _cfg(confirm=2, confirm_clear=4, cooldown=0.0)
    pipeline = build_pipeline(
        config,
        clock=clock,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(_make_disk()),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert isinstance(pipeline, MonitoringPipeline)


def test_when_confirm_samples_clear_set_to_four_then_single_normal_does_not_clear_warn():
    """confirm_samples_clear=4 in MonitorConfig is forwarded to DefaultThresholdEngine (AC1).

    If build_pipeline correctly wires DefaultThresholdEngine using the given MonitorConfig,
    a single NORMAL sample during sustained WARN must not clear to NORMAL.  A simpler
    evaluator that ignores confirm_samples_clear would return NORMAL prematurely, causing
    this test to fail — proving the engine is actually wired.
    """
    config = _cfg(confirm=2, confirm_clear=4, cooldown=0.0)
    pipeline = _scripted_pipeline(
        pressure_seq=[
            PressureLevel.WARN,     # step 1
            PressureLevel.WARN,     # step 2 — confirm_samples=2 met → WARN
            PressureLevel.NORMAL,   # step 3 — single NORMAL must NOT clear (confirm_clear=4)
        ],
        config=config,
    )
    pipeline.step()   # step 1 — WARN building
    pipeline.step()   # step 2 — WARN confirmed
    result = pipeline.step()  # step 3 — single NORMAL dip
    assert result == SentinelState.WARN


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — End-to-end integration walk asserts against DefaultThresholdEngine
# ═════════════════════════════════════════════════════════════════════════════


def test_when_full_walk_scripted_then_normal_warn_critical_disk_low_normal_emitted():
    """Full NORMAL→WARN→CRITICAL→DISK_LOW→NORMAL walk via DefaultThresholdEngine (AC3).

    DefaultThresholdEngine debounces ALL escalations through confirm_samples.
    With confirm_samples=2 and confirm_samples_clear=1:
      step 1: NORMAL, ok disk   → NORMAL
      step 2: WARN,   ok disk   → NORMAL  (1 of 2; hysteresis not met)
      step 3: WARN,   ok disk   → WARN    (2nd consecutive; confirm_samples=2 met)
      step 4: CRITICAL, ok disk → WARN    (1 of 2; escalation gate building from WARN)
      step 5: CRITICAL, ok disk → CRITICAL(2nd consecutive; confirm_samples=2 met)
      step 6: NORMAL, low disk  → DISK_LOW(de-escalation; confirm_samples_clear=1 met)
      step 7: NORMAL, ok disk   → NORMAL  (de-escalation; confirm_samples_clear=1 met)

    Note: step 4 returns WARN (not CRITICAL) because the escalation gate resets its
    streak after confirming WARN at step 3, requiring two more readings to confirm CRITICAL.
    """
    ok_disk = _make_disk(free_gib=100.0)
    low_disk = _make_disk(free_gib=5.0)  # well below 20 GiB floor

    config = _cfg(confirm=2, confirm_clear=1, cooldown=0.0)
    pipeline = _scripted_pipeline(
        pressure_seq=[
            PressureLevel.NORMAL,    # step 1
            PressureLevel.WARN,      # step 2
            PressureLevel.WARN,      # step 3
            PressureLevel.CRITICAL,  # step 4
            PressureLevel.CRITICAL,  # step 5
            PressureLevel.NORMAL,    # step 6
            PressureLevel.NORMAL,    # step 7
        ],
        disk_seq=[
            ok_disk,   # step 1
            ok_disk,   # step 2
            ok_disk,   # step 3
            ok_disk,   # step 4
            ok_disk,   # step 5
            low_disk,  # step 6 — disk dip
            ok_disk,   # step 7 — disk recovered
        ],
        config=config,
    )

    assert pipeline.step() == SentinelState.NORMAL    # step 1
    assert pipeline.step() == SentinelState.NORMAL    # step 2 — 1 WARN, not confirmed
    assert pipeline.step() == SentinelState.WARN      # step 3 — WARN confirmed
    assert pipeline.step() == SentinelState.WARN      # step 4 — CRITICAL building
    assert pipeline.step() == SentinelState.CRITICAL  # step 5 — CRITICAL confirmed
    assert pipeline.step() == SentinelState.DISK_LOW  # step 6 — disk dip
    assert pipeline.step() == SentinelState.NORMAL    # step 7 — recovery


def test_when_single_warn_sample_via_pipeline_then_state_remains_normal():
    """Hysteresis survives composition: a lone WARN sample does not escape NORMAL (AC3).

    confirm_samples=2 means two consecutive WARN readings are required before the
    state machine transitions to WARN.
    """
    config = _cfg(confirm=2, confirm_clear=4, cooldown=0.0)
    pipeline = _scripted_pipeline(
        pressure_seq=[PressureLevel.WARN],
        config=config,
    )
    assert pipeline.step() == SentinelState.NORMAL


def test_when_disk_below_floor_at_normal_pressure_via_pipeline_then_disk_low_returned():
    """Disk-low path through DefaultThresholdEngine: free < 20 GiB → DISK_LOW (AC3).

    Uses confirm_samples=1 so that a single below-floor reading triggers DISK_LOW
    immediately.  DefaultThresholdEngine debounces disk-low through the escalation
    gate, so confirm_samples=1 is required for a single-step assertion.
    Spec: 'free < 20 GB, any pressure' triggers DISK_LOW.
    """
    low_disk = _make_disk(free_gib=_DISK_FLOOR_GiB - 1.0)
    config = _cfg(confirm=1, confirm_clear=4, cooldown=0.0)
    pipeline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(low_disk),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert pipeline.step() == SentinelState.DISK_LOW


def test_when_memory_varies_in_pipeline_then_state_is_unchanged():
    """Memory is advisory (never a gate): varying MemoryReport keeps state identical (AC3).

    Two scripted pipelines identical in pressure/disk but using different MemoryReport
    values must emit the same SentinelState on every step().
    """
    ok_disk = _make_disk(free_gib=100.0)
    config = _cfg(confirm=2, confirm_clear=4, cooldown=0.0)
    pressure_seq = [PressureLevel.WARN, PressureLevel.WARN]

    pipeline_low_mem = build_pipeline(
        config,
        readers={
            "pressure": _ScriptedPressureReader(list(pressure_seq)),
            "disk": _ConstantDiskReader(ok_disk),
            "memory": _ConstantMemoryReader(_make_memory(used_gib=2.0)),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    pipeline_high_mem = build_pipeline(
        config,
        readers={
            "pressure": _ScriptedPressureReader(list(pressure_seq)),
            "disk": _ConstantDiskReader(ok_disk),
            "memory": _ConstantMemoryReader(_make_memory(used_gib=15.0)),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )

    for _ in pressure_seq:
        assert pipeline_low_mem.step() == pipeline_high_mem.step()


# ── AC3 property: memory-never-a-gate holds for any valid memory figure ───────


@settings(max_examples=50)
@given(
    used_gib=st.floats(
        min_value=0.0,
        max_value=16.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_when_memory_used_varies_with_pressure_and_disk_constant_via_pipeline_then_state_stable(
    used_gib: float,
) -> None:
    """For any MemoryReport.used_bytes the pipeline state depends on pressure/disk alone (AC3).

    Invariant type: stability — memory figures must never change the emitted state.
    Strategy derived from criterion: MemoryReport variation must not change proposed_state.
    confirm_samples=1 so a single WARN immediately confirms and the comparison is clear.
    """
    config = _cfg(confirm=1, confirm_clear=1, cooldown=0.0)
    ok_disk = _make_disk(free_gib=100.0)

    baseline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.WARN),
            "disk": _ConstantDiskReader(ok_disk),
            "memory": _ConstantMemoryReader(_make_memory(used_gib=8.0)),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    varied = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.WARN),
            "disk": _ConstantDiskReader(ok_disk),
            "memory": _ConstantMemoryReader(_make_memory(used_gib=used_gib)),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert baseline.step() == varied.step()


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — Regression: directional cooldown and asymmetric clear survive composition
# ═════════════════════════════════════════════════════════════════════════════


def test_when_critical_arrives_during_warn_cooldown_via_pipeline_then_escalates():
    """CRITICAL confirmed during a WARN cooldown still escalates — directional cooldown survives (AC4).

    Sequence with cooldown=300s and FakeClock advanced 1 s per step:
      t=0, step 1: WARN #1  → NORMAL  (streak building)
      t=1, step 2: WARN #2  → WARN    (confirm_samples=2 met; cooldown starts at t=1)
      t=2, step 3: CRITICAL #1         (t=2 is well within the 300 s cooldown window)
      t=3, step 4: CRITICAL #2 → CRITICAL (escalation must not be blocked by cooldown)

    A non-directional cooldown implementation would block the CRITICAL transition and
    return WARN instead, causing this test to fail.
    """
    clock = FakeClock(start=0.0)
    cooldown = 300.0
    config = _cfg(confirm=2, confirm_clear=4, cooldown=cooldown)

    pipeline = _scripted_pipeline(
        pressure_seq=[
            PressureLevel.WARN,      # step 1
            PressureLevel.WARN,      # step 2 — WARN confirmed
            PressureLevel.CRITICAL,  # step 3
            PressureLevel.CRITICAL,  # step 4 — must escalate
        ],
        config=config,
        clock=clock,
    )

    pipeline.step()         # step 1 at t=0
    clock.advance(1.0)      # t=1
    pipeline.step()         # step 2 — WARN confirmed; cooldown starts
    clock.advance(1.0)      # t=2 (well within 300 s cooldown)
    pipeline.step()         # step 3 — CRITICAL #1
    clock.advance(1.0)      # t=3
    result = pipeline.step()  # step 4 — CRITICAL #2: must escalate despite cooldown
    assert result == SentinelState.CRITICAL


def test_when_lone_normal_during_sustained_warn_via_pipeline_then_does_not_clear():
    """A lone NORMAL during sustained WARN does not clear — asymmetric clear survives (AC4).

    Sequence with confirm_samples=2 and confirm_samples_clear=4:
      step 1: WARN  → NORMAL  (hysteresis building)
      step 2: WARN  → WARN    (confirm_samples=2 met)
      step 3: NORMAL → WARN   (single NORMAL; confirm_samples_clear=4 not met)

    A symmetric evaluator that clears on any NORMAL would return NORMAL at step 3,
    causing this test to fail.
    """
    config = _cfg(confirm=2, confirm_clear=4, cooldown=0.0)
    pipeline = _scripted_pipeline(
        pressure_seq=[
            PressureLevel.WARN,    # step 1
            PressureLevel.WARN,    # step 2 — WARN confirmed
            PressureLevel.NORMAL,  # step 3 — single NORMAL must NOT clear
        ],
        config=config,
    )

    pipeline.step()   # step 1
    pipeline.step()   # step 2 — WARN confirmed
    result = pipeline.step()  # step 3 — lone NORMAL during sustained WARN
    assert result == SentinelState.WARN


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — _EMPTY (pipeline.py:33) and _DUMMY (thresholds.py:82) de-duplicated
# ═════════════════════════════════════════════════════════════════════════════


def test_when_empty_sentinels_inspected_then_at_most_one_private_copy_exists():
    """_EMPTY and _DUMMY must resolve to the same object after de-duplication (AC5).

    Before de-duplication: _EMPTY exists in sentinel.pipeline as one object;
    _DUMMY exists in sentinel.rules.threshold as a different object → FAIL.

    After de-duplication (single source), either:
      (a) both names still exist and are the same object (re-export pattern) → PASS, or
      (b) one or both are removed/moved to a canonical module → PASS.

    The test fails only if BOTH names are accessible AND are distinct objects —
    the exact condition that proves two separate sources still exist.
    """
    pipeline_module = importlib.import_module("sentinel.pipeline")
    threshold_module = importlib.import_module("sentinel.rules.threshold")

    pipeline_sentinel = getattr(pipeline_module, "_EMPTY", None)
    threshold_sentinel = getattr(threshold_module, "_DUMMY", None)

    both_defined = pipeline_sentinel is not None and threshold_sentinel is not None
    if both_defined:
        assert pipeline_sentinel is threshold_sentinel, (
            "_EMPTY in sentinel.pipeline and _DUMMY in sentinel.rules.threshold "
            "must be the same object after de-duplication; currently two distinct "
            "sentinels exist."
        )
