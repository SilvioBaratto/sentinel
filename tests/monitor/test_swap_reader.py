"""Tests for SysctlSwapReader – Issue #2 platform shims.

Derived from acceptance criteria only; no implementation source was read.
The injected `sysctl` callable prevents any real subprocess call.

Assumption: SysctlSwapReader and its return type live in
sentinel.monitor.swap.  The return object exposes .total_bytes, .used_bytes,
.free_bytes (integer bytes).

The sysctl output format matched here is the macOS vm.swapusage line:
  vm.swapusage: total = <val><M|G>  used = <val><M|G>  free = <val><M|G>
An optional trailing "(encrypted)" annotation is ignored.
"""

from hypothesis import given, strategies as st

from sentinel.monitor.swap import SysctlSwapReader

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

_KEY = "vm.swapusage:"


def _raw(total: str, used: str, free: str) -> str:
    return f"{_KEY} total = {total}  used = {used}  free = {free}"


def _reader(raw: str) -> SysctlSwapReader:
    return SysctlSwapReader(sysctl=lambda: raw)


# ---------------------------------------------------------------------------
# Zero case (explicit example from the criterion: "0.00M → 0")
# ---------------------------------------------------------------------------


def test_when_all_swap_values_are_zero_megabytes_then_all_bytes_are_zero():
    report = _reader(_raw("0.00M", "0.00M", "0.00M")).read()
    assert report.total_bytes == 0
    assert report.used_bytes == 0
    assert report.free_bytes == 0


# ---------------------------------------------------------------------------
# M-suffix examples
# ---------------------------------------------------------------------------


def test_when_total_is_1_megabyte_then_total_bytes_is_1048576():
    report = _reader(_raw("1.00M", "0.00M", "1.00M")).read()
    assert report.total_bytes == _MB


def test_when_free_is_512_megabytes_then_free_bytes_is_correct():
    report = _reader(_raw("1024.00M", "512.00M", "512.00M")).read()
    assert report.free_bytes == 512 * _MB


def test_when_used_is_100_megabytes_then_used_bytes_is_correct():
    report = _reader(_raw("512.00M", "100.00M", "412.00M")).read()
    assert report.used_bytes == 100 * _MB


# ---------------------------------------------------------------------------
# G-suffix examples
# ---------------------------------------------------------------------------


def test_when_total_is_1_gigabyte_then_total_bytes_is_1073741824():
    report = _reader(_raw("1.00G", "0.00G", "1.00G")).read()
    assert report.total_bytes == _GB


def test_when_used_is_2_gigabytes_then_used_bytes_is_correct():
    report = _reader(_raw("4.00G", "2.00G", "2.00G")).read()
    assert report.used_bytes == 2 * _GB


# ---------------------------------------------------------------------------
# Property: M-suffix conversion is exact for integer megabyte counts.
# Invariant: bytes == count * 1_048_576 for any non-negative integer count.
# ---------------------------------------------------------------------------


@given(
    total=st.integers(min_value=0, max_value=65_536),
    used=st.integers(min_value=0, max_value=65_536),
    free=st.integers(min_value=0, max_value=65_536),
)
def test_when_integer_megabyte_values_with_M_suffix_then_bytes_are_exact(
    total: int, used: int, free: int
) -> None:
    raw = _raw(f"{total}.00M", f"{used}.00M", f"{free}.00M")
    report = _reader(raw).read()
    assert report.total_bytes == total * _MB
    assert report.used_bytes == used * _MB
    assert report.free_bytes == free * _MB


# ---------------------------------------------------------------------------
# Property: G-suffix conversion is exact for integer gigabyte counts.
# Invariant: bytes == count * 1_073_741_824 for any non-negative integer count.
# ---------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=256))
def test_when_integer_gigabyte_total_with_G_suffix_then_total_bytes_are_exact(
    gib: int,
) -> None:
    raw = _raw(f"{gib}.00G", "0.00G", f"{gib}.00G")
    report = _reader(raw).read()
    assert report.total_bytes == gib * _GB
