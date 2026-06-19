"""Tests for SysctlPressureReader – Issue #2 platform shims.

Derived from acceptance criteria only; no implementation source was read.
The injected `sysctl` callable prevents any real subprocess call.

Assumption: PressureLevel, PressureReadError, and SysctlPressureReader live
in sentinel.monitor.pressure (per the project layout in requirements.md).
"""

import pytest
from hypothesis import given, strategies as st

from sentinel.monitor.pressure import (
    PressureLevel,
    PressureReadError,
    SysctlPressureReader,
)

_PREFIX = "kern.memorystatus_vm_pressure_level:"


def _reader(raw: str) -> SysctlPressureReader:
    return SysctlPressureReader(sysctl=lambda: raw)


# ---------------------------------------------------------------------------
# Happy-path: numeric level → PressureLevel member
# ---------------------------------------------------------------------------


def test_when_sysctl_returns_level_1_then_normal_is_returned():
    assert _reader(f"{_PREFIX} 1").read() == PressureLevel.NORMAL


def test_when_sysctl_returns_level_2_then_warn_is_returned():
    assert _reader(f"{_PREFIX} 2").read() == PressureLevel.WARN


def test_when_sysctl_returns_level_4_then_critical_is_returned():
    assert _reader(f"{_PREFIX} 4").read() == PressureLevel.CRITICAL


# ---------------------------------------------------------------------------
# Fail-safe: unknown / un-emitted levels raise typed PressureReadError
# The caller must treat PressureReadError as no-change, never as CRITICAL.
# ---------------------------------------------------------------------------


def test_when_sysctl_returns_level_3_then_pressure_read_error_is_raised():
    # 3 is never emitted by the kernel (see requirements §1); fail-safe.
    with pytest.raises(PressureReadError):
        _reader(f"{_PREFIX} 3").read()


def test_when_sysctl_returns_unknown_numeric_level_then_pressure_read_error_is_raised():
    with pytest.raises(PressureReadError):
        _reader(f"{_PREFIX} 99").read()


def test_when_sysctl_returns_non_numeric_level_then_pressure_read_error_is_raised():
    with pytest.raises(PressureReadError):
        _reader(f"{_PREFIX} high").read()


def test_when_sysctl_returns_garbage_then_pressure_read_error_is_raised():
    with pytest.raises(PressureReadError):
        _reader("not valid sysctl output").read()


def test_when_sysctl_returns_empty_string_then_pressure_read_error_is_raised():
    with pytest.raises(PressureReadError):
        _reader("").read()


# ---------------------------------------------------------------------------
# Whitespace tolerance: trailing spaces/newlines must not cause errors
# ---------------------------------------------------------------------------


def test_when_sysctl_output_has_trailing_spaces_then_normal_is_still_returned():
    assert _reader(f"{_PREFIX} 1   ").read() == PressureLevel.NORMAL


def test_when_sysctl_output_has_trailing_newline_then_warn_is_still_returned():
    assert _reader(f"{_PREFIX} 2\n").read() == PressureLevel.WARN


def test_when_sysctl_output_has_mixed_trailing_whitespace_then_critical_is_still_returned():
    assert _reader(f"{_PREFIX} 4 \t \n").read() == PressureLevel.CRITICAL


# ---------------------------------------------------------------------------
# Property: whitespace tolerance holds for all valid levels and all
# combinations of trailing whitespace characters.
# ---------------------------------------------------------------------------


@given(
    level_str=st.sampled_from(["1", "2", "4"]),
    padding=st.text(alphabet=" \t\r\n", max_size=8),
)
def test_when_valid_level_has_trailing_whitespace_then_result_matches_trimmed_form(
    level_str: str, padding: str
) -> None:
    expected = _reader(f"{_PREFIX} {level_str}").read()
    with_padding = _reader(f"{_PREFIX} {level_str}{padding}").read()
    assert with_padding == expected
