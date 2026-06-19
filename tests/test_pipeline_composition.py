"""
Source-blind tests for Issue #7: pipeline composition root + end-to-end integration.

Authored against acceptance criteria only — no implementation source was read.
Every test uses scripted fakes injected via build_pipeline(config, readers=readers);
the real OS is never called.

Updated for Issue #9: reconciled against DefaultThresholdEngine semantics.
- confirm_samples=2 requires two consecutive readings to confirm WARN *and* CRITICAL.
- confirm_samples_clear=1 is used in the full walk so single-sample recovery is fast.
- DISK_LOW is debounced through the escalation gate (confirm_samples), not immediate.
- _FakePipelineConfig removed; MonitorConfig used directly throughout.

Skipped (oracle: NOT VERIFIABLE at runtime):
  - lone WARN spike never escapes NORMAL as a standalone property
  - varying memory reader is a no-op on state
  - absence of real OS calls as a runtime assertion
  - SOLID / line-count metrics
"""

from __future__ import annotations

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

# ── constants ────────────────────────────────────────────────────────────────

_GiB = 1024**3
_DISK_FLOOR_GiB = 20.0


# ── local domain builders (mirror of conftest helpers, kept self-contained) ──


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


# ── fake readers (scripted / constant) ───────────────────────────────────────


class _ScriptedPressureReader:
    """Yields PressureLevel values from a pre-loaded sequence, one per .read() call."""

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
    """Yields DiskUsage values from a pre-loaded sequence, one per .read() call."""

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


# ── builder helper ────────────────────────────────────────────────────────────


def _scripted_pipeline(
    pressure_seq: list[PressureLevel],
    disk_seq: list[DiskUsage] | None = None,
    confirm_samples: int = 2,
    confirm_samples_clear: int = 1,
) -> MonitoringPipeline:
    """Build a pipeline whose readers deliver pre-programmed values per step().

    confirm_samples_clear defaults to 1 so de-escalation is fast in these tests,
    matching the behaviour expected by the full-walk integration test.
    """
    n = len(pressure_seq)
    if disk_seq is None:
        disk_seq = [_make_disk(free_gib=100.0)] * n
    config = MonitorConfig(
        confirm_samples=confirm_samples,
        confirm_samples_clear=confirm_samples_clear,
        cooldown=0.0,
        disk_low_floor=int(_DISK_FLOOR_GiB * _GiB),
    )
    return build_pipeline(
        config,
        readers={
            "pressure": _ScriptedPressureReader(pressure_seq),
            "disk": _ScriptedDiskReader(disk_seq),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  AC1 — build_pipeline constructs and wires adapters
# ═══════════════════════════════════════════════════════════════════════════════


def test_when_build_pipeline_called_with_fake_readers_then_monitoring_pipeline_is_returned():
    """build_pipeline(config, readers=...) returns a MonitoringPipeline (AC1)."""
    config = MonitorConfig()
    pipeline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(_make_disk()),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert isinstance(pipeline, MonitoringPipeline)


def test_when_build_pipeline_called_without_readers_then_monitoring_pipeline_is_returned():
    """build_pipeline(config) with readers=None (default) returns a MonitoringPipeline (AC1).

    Assumption: the default value readers=None is accepted without raising at construction
    time; calling step() on the result is not exercised here since real OS adapters
    are not available in CI.
    """
    pipeline = build_pipeline(MonitorConfig())
    assert isinstance(pipeline, MonitoringPipeline)


# ═══════════════════════════════════════════════════════════════════════════════
#  AC2 — MonitoringPipeline.step() -> SentinelState, single-pass (no loop)
# ═══════════════════════════════════════════════════════════════════════════════


def test_when_step_called_then_sentinel_state_is_returned():
    """step() returns a SentinelState (AC2)."""
    pipeline = _scripted_pipeline([PressureLevel.NORMAL])
    result = pipeline.step()
    assert isinstance(result, SentinelState)


def test_when_step_called_multiple_times_each_call_returns_a_state():
    """Each step() call returns exactly one SentinelState; no internal looping (AC2).

    If step() were a loop it would exhaust the scripted reader on the first call and
    raise StopIteration (or similar) on the second — demonstrating single-pass contract.
    """
    pipeline = _scripted_pipeline(
        [PressureLevel.NORMAL, PressureLevel.WARN, PressureLevel.WARN],
        confirm_samples=2,
    )
    assert isinstance(pipeline.step(), SentinelState)
    assert isinstance(pipeline.step(), SentinelState)
    assert isinstance(pipeline.step(), SentinelState)


# ── AC2 property — step() is total over its entire stated domain ──────────────


@settings(max_examples=100)
@given(
    pressure=st.sampled_from(list(PressureLevel)),
    disk_free_gib=st.floats(
        min_value=0.01,
        max_value=2_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_when_step_called_with_any_valid_pressure_and_disk_then_sentinel_state_is_returned(
    pressure: PressureLevel,
    disk_free_gib: float,
) -> None:
    """step() never raises for any valid (PressureLevel, disk_free_gib) pair (AC2 invariant).

    Invariant type: never-raises-for-valid-input.
    Derived from: 'step() -> SentinelState runs one pass … no loop/scheduler'.
    """
    pipeline = build_pipeline(
        MonitorConfig(),
        readers={
            "pressure": _ConstantPressureReader(pressure),
            "disk": _ConstantDiskReader(_make_disk(free_gib=disk_free_gib)),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    result = pipeline.step()
    assert isinstance(result, SentinelState)


# ═══════════════════════════════════════════════════════════════════════════════
#  AC3 — Integration sequence: NORMAL → WARN → CRITICAL → DISK_LOW → NORMAL
# ═══════════════════════════════════════════════════════════════════════════════


def test_when_single_warn_sample_then_state_remains_normal():
    """A lone WARN reading does not escape NORMAL; hysteresis requires confirm_samples (AC3).

    Assumption: confirm_samples=2 means two *consecutive* WARN readings are needed
    before the machine moves to WARN.  A single WARN at an otherwise-NORMAL baseline
    is absorbed by the hysteresis window and the emitted state is NORMAL.
    """
    pipeline = _scripted_pipeline([PressureLevel.WARN], confirm_samples=2)
    assert pipeline.step() == SentinelState.NORMAL


def test_when_scripted_sequence_then_normal_warn_critical_disk_low_normal_emitted():
    """Full integration path driven by DefaultThresholdEngine (AC3).

    With confirm_samples=2 and confirm_samples_clear=1:
      step 1: NORMAL, ok disk   → NORMAL
      step 2: WARN,   ok disk   → NORMAL  (1 of 2; hysteresis not met)
      step 3: WARN,   ok disk   → WARN    (2nd consecutive; confirm_samples=2 met)
      step 4: CRITICAL, ok disk → WARN    (1 of 2; escalation gate building)
      step 5: CRITICAL, ok disk → CRITICAL(2nd consecutive; confirm_samples=2 met)
      step 6: NORMAL, low disk  → DISK_LOW(de-escalation; confirm_samples_clear=1 met)
      step 7: NORMAL, ok disk   → NORMAL  (de-escalation; confirm_samples_clear=1 met)

    Note: DefaultThresholdEngine debounces ALL escalations (WARN, CRITICAL, DISK_LOW)
    through the same confirm_samples gate.  Steps 4 and 5 both carry CRITICAL pressure
    because the first CRITICAL reading only advances the streak counter.
    """
    ok_disk = _make_disk(free_gib=100.0)
    low_disk = _make_disk(free_gib=5.0)  # well below 20 GiB floor

    pressure_seq = [
        PressureLevel.NORMAL,  # step 1
        PressureLevel.WARN,  # step 2
        PressureLevel.WARN,  # step 3
        PressureLevel.CRITICAL,  # step 4
        PressureLevel.CRITICAL,  # step 5
        PressureLevel.NORMAL,  # step 6
        PressureLevel.NORMAL,  # step 7
    ]
    disk_seq = [
        ok_disk,  # step 1
        ok_disk,  # step 2
        ok_disk,  # step 3
        ok_disk,  # step 4
        ok_disk,  # step 5
        low_disk,  # step 6 — disk dip
        ok_disk,  # step 7 — disk recovered
    ]

    pipeline = _scripted_pipeline(
        pressure_seq, disk_seq, confirm_samples=2, confirm_samples_clear=1
    )

    assert pipeline.step() == SentinelState.NORMAL  # step 1
    assert pipeline.step() == SentinelState.NORMAL  # step 2 — WARN not yet confirmed
    assert pipeline.step() == SentinelState.WARN  # step 3 — confirm_samples met
    assert pipeline.step() == SentinelState.WARN  # step 4 — CRITICAL building
    assert pipeline.step() == SentinelState.CRITICAL  # step 5 — CRITICAL confirmed
    assert pipeline.step() == SentinelState.DISK_LOW  # step 6 — disk dip
    assert pipeline.step() == SentinelState.NORMAL  # step 7 — recovery


# ═══════════════════════════════════════════════════════════════════════════════
#  AC4 — Disk dip below floor at NORMAL pressure → DISK_LOW
# ═══════════════════════════════════════════════════════════════════════════════


def test_when_disk_free_below_floor_at_normal_pressure_then_disk_low_is_returned():
    """Disk free < floor with NORMAL pressure → DISK_LOW (AC4).

    Uses confirm_samples=1 so a single below-floor reading immediately triggers DISK_LOW.
    DefaultThresholdEngine debounces DISK_LOW through the escalation gate; confirm_samples=1
    ensures a single reading is enough to confirm.
    """
    below_floor = _make_disk(free_gib=_DISK_FLOOR_GiB - 1.0)
    config = MonitorConfig(
        confirm_samples=1,
        confirm_samples_clear=4,
        cooldown=0.0,
        disk_low_floor=int(_DISK_FLOOR_GiB * _GiB),
    )
    pipeline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(below_floor),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert pipeline.step() == SentinelState.DISK_LOW


def test_when_disk_free_at_floor_boundary_then_normal_is_returned():
    """Disk free exactly at the floor (not below) with NORMAL pressure → NORMAL, not DISK_LOW.

    Assumption: the trigger condition is strict: free_bytes < floor_bytes (less-than,
    not less-than-or-equal).  At boundary the system stays NORMAL.
    """
    at_floor = _make_disk(free_gib=_DISK_FLOOR_GiB)
    config = MonitorConfig(
        confirm_samples=1,
        confirm_samples_clear=4,
        cooldown=0.0,
        disk_low_floor=int(_DISK_FLOOR_GiB * _GiB),
    )
    pipeline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(at_floor),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert pipeline.step() == SentinelState.NORMAL


def test_when_disk_free_above_floor_at_normal_pressure_then_normal_is_returned():
    """Disk above floor with NORMAL pressure → NORMAL (complement of DISK_LOW rule, AC4)."""
    above_floor = _make_disk(free_gib=_DISK_FLOOR_GiB + 50.0)
    config = MonitorConfig(
        confirm_samples=1,
        confirm_samples_clear=4,
        cooldown=0.0,
        disk_low_floor=int(_DISK_FLOOR_GiB * _GiB),
    )
    pipeline = build_pipeline(
        config,
        readers={
            "pressure": _ConstantPressureReader(PressureLevel.NORMAL),
            "disk": _ConstantDiskReader(above_floor),
            "memory": _ConstantMemoryReader(_make_memory()),
            "swap": _ConstantSwapReader(_make_swap()),
        },
    )
    assert pipeline.step() == SentinelState.NORMAL
