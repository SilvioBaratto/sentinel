"""Tests for PsutilDiskReader – Issue #2 platform shims.

Derived from acceptance criteria only; no implementation source was read.
The injected `disk_usage` callable prevents any real psutil call.

Criterion: PsutilDiskReader.read(mount) → DiskUsage(mount, free_bytes, total_bytes)

Assumption: DiskUsage and PsutilDiskReader live in sentinel.monitor.disk.
The injected callable signature mirrors psutil.disk_usage:
    disk_usage(path: str) → object with .free (int) and .total (int).
"""

from types import SimpleNamespace

from hypothesis import given, strategies as st

from sentinel.monitor.disk import DiskUsage, PsutilDiskReader

_MB = 1024 * 1024


def _fake_du(free: int, total: int) -> SimpleNamespace:
    return SimpleNamespace(free=free, total=total, used=total - free)


def _reader(free: int = 0, total: int = 0) -> PsutilDiskReader:
    stub = _fake_du(free, total)
    return PsutilDiskReader(disk_usage=lambda _mount: stub)


# ---------------------------------------------------------------------------
# Return-type and field contracts
# ---------------------------------------------------------------------------


def test_when_disk_read_then_result_is_disk_usage_instance():
    result = _reader(free=500 * _MB, total=1000 * _MB).read("/")
    assert isinstance(result, DiskUsage)


def test_when_disk_read_then_mount_equals_argument():
    result = _reader().read("/")
    assert result.mount == "/"


def test_when_disk_read_with_external_mount_then_mount_is_preserved():
    result = _reader().read("/Volumes/External")
    assert result.mount == "/Volumes/External"


def test_when_fake_free_is_300mb_then_result_free_bytes_equals_300mb():
    result = PsutilDiskReader(
        disk_usage=lambda _: _fake_du(free=300 * _MB, total=1000 * _MB)
    ).read("/")
    assert result.free_bytes == 300 * _MB


def test_when_fake_total_is_1000mb_then_result_total_bytes_equals_1000mb():
    result = PsutilDiskReader(
        disk_usage=lambda _: _fake_du(free=300 * _MB, total=1000 * _MB)
    ).read("/")
    assert result.total_bytes == 1000 * _MB


def test_when_disk_is_completely_free_then_free_bytes_equals_total_bytes():
    result = PsutilDiskReader(
        disk_usage=lambda _: _fake_du(free=500 * _MB, total=500 * _MB)
    ).read("/")
    assert result.free_bytes == result.total_bytes


# ---------------------------------------------------------------------------
# Property: the mount path is always echoed back exactly, for any input.
# Invariant: result.mount == mount for all non-empty mount paths.
# ---------------------------------------------------------------------------


@given(st.text(min_size=1))
def test_when_any_mount_path_is_given_then_result_mount_equals_input(
    mount: str,
) -> None:
    reader = PsutilDiskReader(disk_usage=lambda _: _fake_du(free=0, total=0))
    result = reader.read(mount)
    assert result.mount == mount
