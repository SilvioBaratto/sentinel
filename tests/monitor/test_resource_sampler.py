"""
Source-blind tests for DefaultResourceSampler (Issue #3).

Authored against acceptance criteria only — no implementation source was read.

API assumption:
    DefaultResourceSampler(
        clock,          # protocol: .now() → float
        pressure_reader,# protocol: .read() → PressureLevel
        swap_reader,    # protocol: .read() → SwapUsage
        memory_reader,  # protocol: .read() → MemoryReport
        disk_reader,    # protocol: .read(mount: str) → DiskUsage
        mounts,         # tuple[str, ...] of mount paths to sample
    )

    sample() → ResourceSample

The disk_reader is called once per mount path (one call per mount, in order);
all other readers are called exactly once per sample() invocation.

If the constructor keyword names differ in the implementation, rename the
kwargs in these tests; every behavioural assertion remains valid.

Module path assumption: sentinel.monitor.sampler
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from hypothesis import given, strategies as st

from sentinel.domain.value_objects import (
    DiskUsage,
    MemoryReport,
    PressureLevel,
    ResourceSample,
    SwapUsage,
)
from sentinel.monitor.pressure import PressureReadError

from sentinel.monitor.sampler import DefaultResourceSampler


# ── Constants ─────────────────────────────────────────────────────────────────

_GiB = 1024**3


# ── Stubs (built from criteria, not from implementation) ──────────────────────


class _StubClock:
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def now(self) -> float:
        return self._ts


class _CountingPressureReader:
    def __init__(self, level: PressureLevel) -> None:
        self._level = level
        self.call_count = 0

    def read(self) -> PressureLevel:
        self.call_count += 1
        return self._level


class _RaisingPressureReader:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def read(self) -> PressureLevel:
        raise self._exc


class _CountingSwapReader:
    def __init__(self) -> None:
        self._result = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        self.call_count = 0

    def read(self) -> SwapUsage:
        self.call_count += 1
        return self._result


class _CountingMemoryReader:
    def __init__(self) -> None:
        self._result = MemoryReport(
            total_bytes=16 * _GiB,
            used_bytes=8 * _GiB,
            free_bytes=8 * _GiB,
        )
        self.call_count = 0

    def read(self) -> MemoryReport:
        self.call_count += 1
        return self._result


class _TrackingDiskReader:
    """Records each (mount) argument in call order; returns a DiskUsage per call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read(self, mount: str) -> DiskUsage:
        self.calls.append(mount)
        return DiskUsage(mount=mount, free_bytes=50 * _GiB, total_bytes=200 * _GiB)


# ── Builder ───────────────────────────────────────────────────────────────────


def _make_sampler(
    *,
    ts: float = 0.0,
    pressure: PressureLevel = PressureLevel.NORMAL,
    mounts: tuple[str, ...] = ("/",),
    pressure_reader: Any = None,
    disk_reader: Any = None,
    swap_reader: Any = None,
    memory_reader: Any = None,
) -> DefaultResourceSampler:
    return DefaultResourceSampler(
        clock=_StubClock(ts),
        pressure_reader=pressure_reader or _CountingPressureReader(pressure),
        swap_reader=swap_reader or _CountingSwapReader(),
        memory_reader=memory_reader or _CountingMemoryReader(),
        disk_reader=disk_reader or _TrackingDiskReader(),
        mounts=mounts,
    )


# ── Criterion 1: sample() returns a ResourceSample stamped with the injected clock ───


def test_when_sampled_then_result_is_resource_sample():
    result = _make_sampler().sample()
    assert isinstance(result, ResourceSample)


def test_when_clock_returns_42_then_timestamp_is_42():
    result = _make_sampler(ts=42.0).sample()
    assert result.timestamp == 42.0


def test_when_clock_returns_zero_then_timestamp_is_zero():
    result = _make_sampler(ts=0.0).sample()
    assert result.timestamp == 0.0


def test_when_sampled_then_pressure_reader_called_exactly_once():
    reader = _CountingPressureReader(PressureLevel.NORMAL)
    _make_sampler(pressure_reader=reader).sample()
    assert reader.call_count == 1


def test_when_sampled_then_swap_reader_called_exactly_once():
    reader = _CountingSwapReader()
    _make_sampler(swap_reader=reader).sample()
    assert reader.call_count == 1


def test_when_sampled_then_memory_reader_called_exactly_once():
    reader = _CountingMemoryReader()
    _make_sampler(memory_reader=reader).sample()
    assert reader.call_count == 1


# ── Criterion 2: reader called exactly once per mount; disks tuple matches ────


def test_when_one_mount_then_disk_reader_called_exactly_once():
    reader = _TrackingDiskReader()
    _make_sampler(mounts=("/",), disk_reader=reader).sample()
    assert len(reader.calls) == 1


def test_when_two_mounts_then_disk_reader_called_exactly_twice():
    reader = _TrackingDiskReader()
    _make_sampler(mounts=("/", "/Volumes/Data"), disk_reader=reader).sample()
    assert len(reader.calls) == 2


def test_when_zero_mounts_then_disk_reader_never_called():
    reader = _TrackingDiskReader()
    _make_sampler(mounts=(), disk_reader=reader).sample()
    assert len(reader.calls) == 0


def test_when_two_mounts_then_disks_is_a_tuple():
    result = _make_sampler(mounts=("/", "/Volumes/Data")).sample()
    assert isinstance(result.disks, tuple)


def test_when_two_mounts_then_disks_tuple_has_length_two():
    result = _make_sampler(mounts=("/", "/Volumes/Data")).sample()
    assert len(result.disks) == 2


def test_when_zero_mounts_then_disks_is_empty_tuple():
    result = _make_sampler(mounts=()).sample()
    assert result.disks == ()


def test_when_two_mounts_then_first_disk_mount_matches_first_injected_mount():
    result = _make_sampler(mounts=("/", "/Volumes/External")).sample()
    assert result.disks[0].mount == "/"


def test_when_two_mounts_then_second_disk_mount_matches_second_injected_mount():
    result = _make_sampler(mounts=("/", "/Volumes/External")).sample()
    assert result.disks[1].mount == "/Volumes/External"


def test_when_two_mounts_then_reader_call_order_matches_mount_order():
    reader = _TrackingDiskReader()
    _make_sampler(mounts=("/", "/Volumes/Data"), disk_reader=reader).sample()
    assert reader.calls == ["/", "/Volumes/Data"]


@given(st.lists(st.text(min_size=1, max_size=32), min_size=0, max_size=6))
def test_when_n_mounts_injected_then_disks_tuple_length_equals_n(
    mount_paths: list[str],
) -> None:
    """Length invariant: len(result.disks) == len(mounts) for any mount list."""
    result = _make_sampler(mounts=tuple(mount_paths)).sample()
    assert len(result.disks) == len(mount_paths)


# ── Criterion 3: ResourceSample.pressure equals exactly what PressureReader returned ──


def test_when_pressure_reader_returns_normal_then_sample_pressure_is_normal():
    result = _make_sampler(pressure=PressureLevel.NORMAL).sample()
    assert result.pressure == PressureLevel.NORMAL


def test_when_pressure_reader_returns_warn_then_sample_pressure_is_warn():
    result = _make_sampler(pressure=PressureLevel.WARN).sample()
    assert result.pressure == PressureLevel.WARN


def test_when_pressure_reader_returns_critical_then_sample_pressure_is_critical():
    result = _make_sampler(pressure=PressureLevel.CRITICAL).sample()
    assert result.pressure == PressureLevel.CRITICAL


@given(st.sampled_from(list(PressureLevel)))
def test_when_pressure_reader_returns_any_level_then_sample_pressure_equals_that_level(
    level: PressureLevel,
) -> None:
    """Passthrough invariant: no mutation or thresholding in the sampler."""
    result = _make_sampler(pressure=level).sample()
    assert result.pressure == level


# ── Criterion 4: raising pressure reader surfaces exception (not silently swallowed) ──


def test_when_pressure_reader_raises_pressure_read_error_then_sample_surfaces_it():
    """
    Fail-safe policy (requirements §Non-functional): errors from readers must
    propagate — the sampler must not catch and swallow them.
    """
    reader = _RaisingPressureReader(PressureReadError("sysctl unavailable"))
    sampler = _make_sampler(pressure_reader=reader)
    with pytest.raises(PressureReadError):
        sampler.sample()


def test_when_pressure_reader_raises_os_error_then_sample_surfaces_it():
    """Confirms fail-safe surfacing is not restricted to PressureReadError."""
    reader = _RaisingPressureReader(OSError("sysctl call failed"))
    sampler = _make_sampler(pressure_reader=reader)
    with pytest.raises(OSError):
        sampler.sample()


def test_when_pressure_reader_raises_then_exception_identity_is_preserved():
    exc = PressureReadError("identity check")
    reader = _RaisingPressureReader(exc)
    sampler = _make_sampler(pressure_reader=reader)
    with pytest.raises(PressureReadError) as exc_info:
        sampler.sample()
    assert exc_info.value is exc


# ── Criterion 5: sample() body < 10 non-blank / non-comment physical lines ────


def test_when_sample_method_inspected_then_body_is_fewer_than_10_lines():
    """
    Counts non-blank, non-comment physical lines in sample() body,
    excluding the 'def' line itself.  Uses inspect at test-run time so the
    check is driven by the actual implementation, not a source snapshot.
    """
    source_lines, _ = inspect.getsourcelines(DefaultResourceSampler.sample)
    body = [
        ln
        for ln in source_lines[1:]  # skip 'def sample(...):'
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert len(body) < 10, f"sample() has {len(body)} body lines — must be < 10"
