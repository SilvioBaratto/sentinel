"""Tests for sentinel.fmt.format_bytes — SI decimal human-readable byte formatter."""

from __future__ import annotations

from hypothesis import given, strategies as st

from sentinel.fmt import format_bytes


class TestFormatBytesExamples:
    def test_when_bytes_is_zero_then_zero_b_is_returned(self) -> None:
        assert format_bytes(0) == "0 B"

    def test_when_bytes_is_less_than_1000_then_b_unit_is_used(self) -> None:
        assert format_bytes(999) == "999 B"

    def test_when_bytes_is_1000_then_1_kb_is_returned(self) -> None:
        assert format_bytes(1_000) == "1.0 KB"

    def test_when_bytes_is_1_200_000_000_then_1_2_gb_is_returned(self) -> None:
        """Criterion example: 1.2 GB."""
        assert format_bytes(1_200_000_000) == "1.2 GB"

    def test_when_bytes_is_1_000_000_then_1_mb_is_returned(self) -> None:
        assert format_bytes(1_000_000) == "1.0 MB"

    def test_when_bytes_is_1_000_000_000_then_1_gb_is_returned(self) -> None:
        assert format_bytes(1_000_000_000) == "1.0 GB"

    def test_when_bytes_is_1_500_000_then_1_5_mb_is_returned(self) -> None:
        assert format_bytes(1_500_000) == "1.5 MB"


class TestFormatBytesProperties:
    @given(st.integers(min_value=0, max_value=10 * 1024**4))
    def test_when_any_non_negative_bytes_given_then_no_error_is_raised(
        self, n: int
    ) -> None:
        format_bytes(n)  # must not raise

    @given(st.integers(min_value=0, max_value=10 * 1024**4))
    def test_when_any_non_negative_bytes_given_then_result_is_non_empty_string(
        self, n: int
    ) -> None:
        result = format_bytes(n)
        assert isinstance(result, str) and len(result) > 0

    @given(st.integers(min_value=0, max_value=999))
    def test_when_bytes_below_1000_then_b_unit_appears(self, n: int) -> None:
        assert format_bytes(n).endswith(" B") or format_bytes(n) == f"{n} B"
