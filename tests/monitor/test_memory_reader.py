"""Tests for PsutilMemoryReader — Issue #8 domain-type alignment.

Replaces Issue #2's test_memory_reader.py.  All assertions use the canonical
domain MemoryReport fields (total_bytes / used_bytes / free_bytes).  The psutil
internals .available and .percent must NOT appear in MemoryReport assertions.

Assumption (free_bytes mapping): free_bytes is derived from
psutil.virtual_memory().available — the memory that can be given to processes
without triggering swap.  On macOS, 'used' includes compressed / swapped pages
so ``total - used`` overstates true consumption; 'available' is the operationally
correct measure.  The [3-0] constraint applies: psutil is reporting-only and
must never gate the state machine.
"""

from __future__ import annotations

from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

from sentinel.domain.protocols import MemoryReader
from sentinel.domain.value_objects import (
    DiskUsage,
    MemoryReport,
    PressureLevel,
    ResourceSample,
    SentinelState,
    SwapUsage,
)
from sentinel.monitor.memory import PsutilMemoryReader

_GB = 1024 * 1024 * 1024
_16GB = 16 * _GB
_8GB = 8 * _GB


# ── helpers ──────────────────────────────────────────────────────────────────


def _fake_vm(
    total: int = _16GB,
    available: int = _8GB,
    used: int = _8GB,
    percent: float = 50.0,
) -> SimpleNamespace:
    """Fake psutil.virtual_memory() result — prevents any real OS call."""
    return SimpleNamespace(total=total, available=available, used=used, percent=percent)


def _reader(**kwargs) -> PsutilMemoryReader:
    return PsutilMemoryReader(virtual_memory=lambda: _fake_vm(**kwargs))


# ── AC1: domain MemoryReport is used; local redefinition deleted ──────────────


def test_when_memory_read_then_result_is_exact_domain_memory_report_type():
    """read() must return the domain MemoryReport, not a separately-defined local class.

    ``type(result) is MemoryReport`` (strict identity, not isinstance) catches the case
    where a residual local MemoryReport subclass is returned instead of the domain type.
    """
    result = _reader().read()
    assert type(result) is MemoryReport


# ── AC3: MemoryReader protocol is satisfied ───────────────────────────────────


def test_when_psutil_reader_checked_against_protocol_then_memory_reader_protocol_is_satisfied():
    """PsutilMemoryReader must be an isinstance match for the MemoryReader Protocol (AC3)."""
    assert isinstance(_reader(), MemoryReader)


# ── AC2: domain field mapping — total_bytes / used_bytes / free_bytes ─────────


def test_when_total_is_16gb_then_total_bytes_equals_16gb():
    """psutil.total is propagated to MemoryReport.total_bytes (AC2)."""
    assert _reader(total=_16GB).read().total_bytes == _16GB


def test_when_used_is_8gb_then_used_bytes_equals_8gb():
    """psutil.used is propagated to MemoryReport.used_bytes (AC2)."""
    assert _reader(used=_8GB).read().used_bytes == _8GB


def test_when_available_is_4gb_then_free_bytes_equals_4gb():
    """psutil.available is mapped to MemoryReport.free_bytes (AC2).

    Assumption: free_bytes = psutil.available.  See module docstring for rationale.
    """
    assert _reader(available=4 * _GB).read().free_bytes == 4 * _GB


def test_when_available_is_zero_then_free_bytes_is_zero():
    """Edge case: zero available memory propagates to free_bytes=0 (AC2)."""
    assert _reader(available=0).read().free_bytes == 0


# ── AC4: no .available / .percent on the domain MemoryReport ─────────────────


def test_when_domain_memory_report_is_inspected_then_available_attribute_is_absent():
    """MemoryReport must NOT expose .available — that is a psutil-internal field (AC4)."""
    report = MemoryReport(total_bytes=_16GB, used_bytes=_8GB, free_bytes=_8GB)
    assert not hasattr(report, "available")


def test_when_domain_memory_report_is_inspected_then_percent_attribute_is_absent():
    """MemoryReport must NOT expose .percent — that is a psutil-internal field (AC4)."""
    report = MemoryReport(total_bytes=_16GB, used_bytes=_8GB, free_bytes=_8GB)
    assert not hasattr(report, "percent")


# ── AC5: end-to-end AttributeError regression guard ──────────────────────────


def test_when_memory_reader_sample_fed_through_threshold_engine_then_no_attribute_error():
    """A sample built with PsutilMemoryReader must not cause AttributeError in the engine.

    DefaultThresholdEngine._reason() accesses sample.memory.used_bytes.  Before
    this fix, PsutilMemoryReader returned a local MemoryReport with .used (not
    .used_bytes), causing AttributeError on every evaluate() call.  (AC5)

    WARN pressure is deliberate: _reason() formats memory on every code path,
    but WARN makes the intent of the test obvious — we are testing the full
    evaluate() path, not just that the engine starts up.
    """
    from sentinel.config import MonitorConfig
    from sentinel.monitor.rolling_history import RollingHistory
    from sentinel.rules.thresholds import DefaultThresholdEngine

    memory = _reader().read()
    sample = ResourceSample(
        timestamp=1.0,
        pressure=PressureLevel.WARN,
        swap=SwapUsage(0, 0, 0),
        disks=(DiskUsage("/", 100 * _GB, 500 * _GB),),
        memory=memory,
    )
    config = MonitorConfig()
    engine = DefaultThresholdEngine(config, lambda: 0.0)
    history = RollingHistory(config)
    history.append(sample)

    signal = engine.evaluate(history)  # must not raise AttributeError
    assert signal is not None


# ── AC6: memory is reporting-only; it must never gate proposed_state ──────────


def test_when_memory_values_differ_then_proposed_state_is_identical():
    """Samples with identical pressure + disk but opposite memory → same proposed_state.

    The [3-0] psutil-never-a-gate constraint: MemoryReport values must be
    structurally unable to influence proposed_state. (AC6)
    """
    from sentinel.config import MonitorConfig
    from sentinel.monitor.rolling_history import RollingHistory
    from sentinel.rules.thresholds import DefaultThresholdEngine

    def _sample(total: int, used: int, available: int) -> ResourceSample:
        memory = _reader(total=total, used=used, available=available).read()
        return ResourceSample(
            timestamp=1.0,
            pressure=PressureLevel.NORMAL,
            swap=SwapUsage(0, 0, 0),
            disks=(DiskUsage("/", 100 * _GB, 500 * _GB),),
            memory=memory,
        )

    config = MonitorConfig()
    states = []
    for sample in (_sample(_16GB, _16GB, 0), _sample(_16GB, 0, _16GB)):
        engine = DefaultThresholdEngine(config, lambda: 0.0)
        history = RollingHistory(config)
        history.append(sample)
        states.append(engine.evaluate(history).proposed_state)

    assert states[0] == states[1]


@settings(max_examples=60)
@given(
    total=st.integers(min_value=0, max_value=64 * _GB),
    used=st.integers(min_value=0, max_value=64 * _GB),
    available=st.integers(min_value=0, max_value=64 * _GB),
)
def test_when_any_memory_values_at_normal_pressure_then_proposed_state_is_always_normal(
    total: int,
    used: int,
    available: int,
) -> None:
    """For any MemoryReport values, NORMAL pressure + adequate disk → NORMAL state.

    Invariant type: never-gates — no combination of memory values can push the
    state machine away from NORMAL when pressure is NORMAL and disk is within
    bounds.  (AC6 property)
    """
    from sentinel.config import MonitorConfig
    from sentinel.monitor.rolling_history import RollingHistory
    from sentinel.rules.thresholds import DefaultThresholdEngine

    fake = _fake_vm(total=total, used=used, available=available)
    memory = PsutilMemoryReader(virtual_memory=lambda: fake).read()
    sample = ResourceSample(
        timestamp=1.0,
        pressure=PressureLevel.NORMAL,
        swap=SwapUsage(0, 0, 0),
        disks=(DiskUsage("/", 100 * _GB, 500 * _GB),),
        memory=memory,
    )
    config = MonitorConfig()
    engine = DefaultThresholdEngine(config, lambda: 0.0)
    history = RollingHistory(config)
    history.append(sample)

    assert engine.evaluate(history).proposed_state == SentinelState.NORMAL
