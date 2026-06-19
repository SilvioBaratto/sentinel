"""
Tests for RollingHistory — bounded rolling history buffer (Issue #4).

Module paths:
  sentinel.monitor.rolling_history → RollingHistory
  sentinel.domain.value_objects    → ResourceSample (the real domain type)
"""

import dataclasses

from hypothesis import given, strategies as st

from sentinel.domain.value_objects import (
    MemoryReport,
    PressureLevel,
    ResourceSample,
    SwapUsage,
)
from sentinel.monitor.rolling_history import RollingHistory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Cfg:
    """Minimal config stub — exposes only the attribute RollingHistory needs."""

    history_size: int


def _make_sample(identifier: float = 0.0) -> ResourceSample:
    """Build a ResourceSample with a distinct timestamp so objects compare unequal."""
    return ResourceSample(
        timestamp=identifier,
        pressure=PressureLevel.NORMAL,
        swap=SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0),
        disks=(),
        memory=MemoryReport(total_bytes=0, used_bytes=0, free_bytes=0),
    )


# ---------------------------------------------------------------------------
# Criterion 1: bounded deque — len <= maxlen; oldest evicted FIFO when full
# ---------------------------------------------------------------------------


class TestBoundedDeque:
    def test_when_buffer_is_new_then_len_is_zero(self) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        assert len(h) == 0

    def test_when_appends_equal_maxlen_then_len_equals_maxlen(self) -> None:
        h = RollingHistory(_Cfg(history_size=3))
        for i in range(3):
            h.append(_make_sample(float(i)))
        assert len(h) == 3

    def test_when_appends_exceed_maxlen_then_len_is_capped_at_maxlen(self) -> None:
        h = RollingHistory(_Cfg(history_size=3))
        for i in range(10):
            h.append(_make_sample(float(i)))
        assert len(h) == 3

    def test_when_buffer_full_and_new_sample_appended_then_oldest_is_evicted(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=2))
        s1, s2, s3 = _make_sample(1.0), _make_sample(2.0), _make_sample(3.0)
        h.append(s1)
        h.append(s2)
        h.append(s3)  # s1 must be evicted (FIFO)
        window = h.recent(2)
        assert s1 not in window
        assert s2 in window
        assert s3 in window

    @given(
        st.integers(min_value=1, max_value=20),
        st.integers(min_value=0, max_value=100),
    )
    def test_when_any_number_of_samples_appended_then_len_never_exceeds_maxlen(
        self, maxlen: int, count: int
    ) -> None:
        h = RollingHistory(_Cfg(history_size=maxlen))
        for i in range(count):
            h.append(_make_sample(float(i)))
        assert len(h) <= maxlen


# ---------------------------------------------------------------------------
# Criterion 2: append / recent / latest / __len__ API semantics
# ---------------------------------------------------------------------------


class TestAPIContract:
    def test_when_recent_n_equals_buffer_len_then_tuple_returned_in_chronological_order(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        s1, s2, s3 = _make_sample(1.0), _make_sample(2.0), _make_sample(3.0)
        for s in (s1, s2, s3):
            h.append(s)
        assert h.recent(3) == (s1, s2, s3)

    def test_when_recent_n_greater_than_buffer_len_then_all_items_are_returned(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        s1, s2 = _make_sample(1.0), _make_sample(2.0)
        h.append(s1)
        h.append(s2)
        assert h.recent(100) == (s1, s2)

    def test_when_recent_n_is_zero_then_empty_tuple_is_returned(self) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        h.append(_make_sample(1.0))
        assert h.recent(0) == ()

    def test_when_recent_called_on_empty_buffer_then_empty_tuple_is_returned(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        assert h.recent(5) == ()

    def test_when_recent_called_then_return_type_is_tuple(self) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        h.append(_make_sample())
        assert isinstance(h.recent(1), tuple)

    def test_when_recent_n_less_than_buffer_len_then_last_n_items_returned_in_chronological_order(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        s1, s2, s3, s4 = (
            _make_sample(1.0),
            _make_sample(2.0),
            _make_sample(3.0),
            _make_sample(4.0),
        )
        for s in (s1, s2, s3, s4):
            h.append(s)
        assert h.recent(2) == (s3, s4)

    def test_when_latest_called_on_nonempty_buffer_then_most_recent_sample_is_returned(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        s1, s2 = _make_sample(1.0), _make_sample(2.0)
        h.append(s1)
        h.append(s2)
        assert h.latest() == s2

    def test_when_latest_called_on_empty_buffer_then_none_is_returned(self) -> None:
        h = RollingHistory(_Cfg(history_size=5))
        assert h.latest() is None

    def test_when_samples_appended_below_maxlen_then_len_increments_by_one_per_append(
        self,
    ) -> None:
        h = RollingHistory(_Cfg(history_size=10))
        for expected in range(1, 6):
            h.append(_make_sample(float(expected)))
            assert len(h) == expected

    @given(
        st.integers(min_value=1, max_value=10),
        st.integers(min_value=0, max_value=30),
    )
    def test_when_samples_appended_then_recent_returns_them_in_fifo_chronological_order(
        self, maxlen: int, count: int
    ) -> None:
        """Ordering invariant: recent(k) returns the last k appended items in append order."""
        h = RollingHistory(_Cfg(history_size=maxlen))
        samples = [_make_sample(float(i)) for i in range(count)]
        for s in samples:
            h.append(s)
        visible = samples[max(0, count - maxlen) :]
        k = len(visible)
        assert h.recent(k) == tuple(visible)
