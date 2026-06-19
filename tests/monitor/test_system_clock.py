"""Tests for SystemClock – Issue #2 platform shims.

Derived from acceptance criteria only; no implementation source was read.
The injected `monotonic` callable prevents any dependency on wall-clock time.

Criterion: SystemClock.now() wraps time.monotonic so cooldown math is immune
to wall-clock jumps.

The "immune to wall-clock jumps" guarantee comes from time.monotonic itself;
these tests verify that SystemClock delegates faithfully to the injected
callable and does so on every call (not caching the first result).

Assumption: SystemClock lives in sentinel.monitor.clock.
Constructor: SystemClock(monotonic: Callable[[], float])
"""

from hypothesis import given, strategies as st

from sentinel.monitor.clock import SystemClock


# ---------------------------------------------------------------------------
# Wrapping contract: now() returns exactly what the callable returns
# ---------------------------------------------------------------------------


def test_when_monotonic_returns_42_then_now_returns_42():
    clock = SystemClock(monotonic=lambda: 42.0)
    assert clock.now() == 42.0


def test_when_monotonic_returns_zero_then_now_returns_zero():
    clock = SystemClock(monotonic=lambda: 0.0)
    assert clock.now() == 0.0


def test_when_monotonic_returns_large_value_then_now_returns_same_large_value():
    clock = SystemClock(monotonic=lambda: 1_000_000.0)
    assert clock.now() == 1_000_000.0


# ---------------------------------------------------------------------------
# Delegation is live: each call to now() re-invokes the callable
# ---------------------------------------------------------------------------


def test_when_monotonic_advances_then_each_now_call_reflects_the_advance():
    readings = iter([10.0, 20.0, 30.0])
    clock = SystemClock(monotonic=lambda: next(readings))
    assert clock.now() == 10.0
    assert clock.now() == 20.0
    assert clock.now() == 30.0


def test_when_monotonic_advances_then_later_now_is_greater_than_earlier_now():
    counter = [0.0]

    def fake_monotonic() -> float:
        counter[0] += 1.0
        return counter[0]

    clock = SystemClock(monotonic=fake_monotonic)
    first = clock.now()
    second = clock.now()
    assert second > first


# ---------------------------------------------------------------------------
# Property: now() == callable() for any float the callable may return.
# Invariant: SystemClock.now() is a pure pass-through to the injected fn.
# ---------------------------------------------------------------------------


@given(
    t=st.floats(
        min_value=0.0,
        max_value=1e12,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_when_monotonic_returns_any_valid_float_then_now_returns_same_value(
    t: float,
) -> None:
    clock = SystemClock(monotonic=lambda: t)
    assert clock.now() == t
