"""Example tests for issue #11.

feat: process & HID OS shims — PsutilProcessLister + ioreg HID idle reader

Two-pass CPU sampling:
  - Pass 1 (prime): cpu_percent(interval=None) arms every process counter.
  - ONE sleep for cpu_sample_interval — O(1) wall-clock regardless of N procs.
  - Pass 2 (read): cpu_percent(interval=None) returns the sustained delta.

Tests use injected fake callables — no real psutil, subprocess, or time.sleep.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------


def _import_psutil_process_lister():
    from sentinel.process.lister import PsutilProcessLister

    return PsutilProcessLister


def _import_ioreg_hid_reader():
    from sentinel.process.hid import IoregHidIdleReader

    return IoregHidIdleReader


def _import_value_objects():
    from sentinel.domain import value_objects

    return value_objects


def _import_config():
    import sentinel.config as cfg

    return cfg


# ---------------------------------------------------------------------------
# Fake psutil proc helpers
# ---------------------------------------------------------------------------


class _FakeMemInfo:
    def __init__(self, rss: int) -> None:
        self.rss = rss


class _FakePsutilProc:
    """Minimal psutil Process mimic that models the two-pass CPU delta pattern.

    First cpu_percent() call (prime) returns 0.0.
    Subsequent calls (read) return self._cpu_pct — the delta value.
    """

    def __init__(
        self,
        pid: int = 100,
        ppid: int = 1,
        name: str = "TestApp",
        cmdline: list[str] | None = None,
        terminal: str | None = None,
        pgid: int | None = None,
        cpu_pct: float = 0.0,
        rss: int = 0,
        create_time: float = 0.0,
    ) -> None:
        self.pid = pid
        self._ppid = ppid
        self._name = name
        self._cmdline = cmdline if cmdline is not None else ["TestApp"]
        self._terminal = terminal
        self._pgid = pgid
        self._cpu_pct = cpu_pct
        self._rss = rss
        self._create_time = create_time
        self.cpu_percent_calls: list[float | None] = []

    def cpu_percent(self, interval: float | None = None) -> float:
        self.cpu_percent_calls.append(interval)
        # First call = prime (returns 0.0); subsequent = read (returns delta).
        return 0.0 if len(self.cpu_percent_calls) == 1 else self._cpu_pct

    def terminal(self) -> str | None:
        return self._terminal

    def ppid(self) -> int:
        return self._ppid

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return self._cmdline

    def memory_info(self) -> _FakeMemInfo:
        return _FakeMemInfo(self._rss)

    def create_time(self) -> float:
        return self._create_time

    def pgid(self) -> int | None:
        return self._pgid


class _RaisingProc:
    """Fake proc that raises on every call — simulates a disappeared process."""

    def __init__(self, pid: int = 999) -> None:
        self.pid = pid

    def cpu_percent(self, interval: float | None = None) -> float:
        raise RuntimeError("process gone")

    def terminal(self) -> str | None:
        raise RuntimeError("process gone")

    def ppid(self) -> int:
        raise RuntimeError("process gone")

    def name(self) -> str:
        raise RuntimeError("process gone")

    def cmdline(self) -> list[str]:
        raise RuntimeError("process gone")

    def memory_info(self) -> _FakeMemInfo:
        raise RuntimeError("process gone")

    def create_time(self) -> float:
        raise RuntimeError("process gone")

    def pgid(self) -> int | None:
        raise RuntimeError("process gone")


# ---------------------------------------------------------------------------
# Fake ioreg helpers
# ---------------------------------------------------------------------------


def _make_ioreg_output(hid_ns: int) -> str:
    return f'"HIDIdleTime" = {hid_ns}\n'


def _make_ioreg_callable(hid_ns: int):
    text = _make_ioreg_output(hid_ns)
    return lambda *a, **kw: text


def _make_garbage_ioreg_callable():
    return lambda *a, **kw: "NOT VALID IOREG OUTPUT\ngarbage data\n!!!\n"


def _make_empty_ioreg_callable():
    return lambda *a, **kw: ""


# ---------------------------------------------------------------------------
# Criterion 1: PsutilProcessLister.list() maps procs to ProcessInfo
# ---------------------------------------------------------------------------


class TestPsutilProcessListerMapping:
    """Criterion: list() -> tuple[ProcessInfo, ...] with all required fields."""

    def _make_lister(self, procs, config=None):
        PsutilProcessLister = _import_psutil_process_lister()
        cfg = _import_config()
        return PsutilProcessLister(
            process_iter=lambda *a, **kw: iter(procs),
            sleep=lambda t: None,
            config=config or cfg.ProcessConfig(),
        )

    def test_when_list_called_then_result_is_tuple(self):
        result = self._make_lister([_FakePsutilProc(pid=1)]).list()
        assert isinstance(result, tuple)

    def test_when_single_proc_present_then_result_contains_one_process_info(self):
        vo = _import_value_objects()
        result = self._make_lister([_FakePsutilProc(pid=1)]).list()
        assert len(result) == 1
        assert isinstance(result[0], vo.ProcessInfo)

    def test_when_proc_has_pid_then_process_info_pid_matches(self):
        result = self._make_lister([_FakePsutilProc(pid=42)]).list()
        assert result[0].pid == 42

    def test_when_proc_has_ppid_then_process_info_ppid_matches(self):
        result = self._make_lister([_FakePsutilProc(ppid=7)]).list()
        assert result[0].ppid == 7

    def test_when_proc_has_name_then_process_info_name_matches(self):
        result = self._make_lister([_FakePsutilProc(name="Safari")]).list()
        assert result[0].name == "Safari"

    def test_when_proc_has_cmdline_then_process_info_cmdline_is_tuple_with_correct_values(
        self,
    ):
        result = self._make_lister(
            [_FakePsutilProc(cmdline=["/usr/bin/python3", "train.py"])]
        ).list()
        assert isinstance(result[0].cmdline, tuple)
        assert result[0].cmdline == ("/usr/bin/python3", "train.py")

    def test_when_proc_terminal_is_set_then_has_tty_is_true_and_tty_matches(self):
        result = self._make_lister([_FakePsutilProc(terminal="/dev/ttys001")]).list()
        assert result[0].has_tty is True
        assert result[0].tty == "/dev/ttys001"

    def test_when_proc_terminal_is_none_then_has_tty_is_false(self):
        result = self._make_lister([_FakePsutilProc(terminal=None)]).list()
        assert result[0].has_tty is False

    def test_when_proc_has_pgid_then_process_info_pgid_matches(self):
        result = self._make_lister([_FakePsutilProc(pgid=50)]).list()
        assert result[0].pgid == 50

    def test_when_proc_has_cpu_pct_then_process_info_cpu_percent_is_the_read_delta(
        self,
    ):
        result = self._make_lister([_FakePsutilProc(cpu_pct=3.5)]).list()
        assert result[0].cpu_percent == pytest.approx(3.5)

    def test_when_proc_has_rss_then_process_info_rss_bytes_matches(self):
        result = self._make_lister([_FakePsutilProc(rss=4 * 1024 * 1024)]).list()
        assert result[0].rss_bytes == 4 * 1024 * 1024

    def test_when_proc_has_create_time_then_process_info_create_time_matches(self):
        result = self._make_lister([_FakePsutilProc(create_time=1700000000.0)]).list()
        assert result[0].create_time == pytest.approx(1700000000.0)

    def test_when_multiple_procs_present_then_all_pids_are_mapped(self):
        procs = [_FakePsutilProc(pid=p) for p in (10, 20, 30)]
        result = self._make_lister(procs).list()
        assert {p.pid for p in result} == {10, 20, 30}

    def test_when_no_procs_present_then_empty_tuple_is_returned(self):
        assert self._make_lister([]).list() == ()


# ---------------------------------------------------------------------------
# Criterion 2: Two-pass CPU sampling — ONE sleep, non-blocking
# ---------------------------------------------------------------------------


class TestCpuSamplingInterval:
    """Criterion: sustained CPU via two-pass + single shared sleep, not O(N) blocking."""

    def _make_lister_and_procs(self, config=None):
        PsutilProcessLister = _import_psutil_process_lister()
        cfg = _import_config()
        process_config = config or cfg.ProcessConfig()
        procs = [_FakePsutilProc(pid=10), _FakePsutilProc(pid=20)]
        sleep_calls: list[float] = []
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter(procs),
            sleep=lambda t: sleep_calls.append(t),
            config=process_config,
        )
        return lister, procs, sleep_calls

    def test_when_list_called_then_sleep_is_called_exactly_once(self):
        lister, _, sleep_calls = self._make_lister_and_procs()
        lister.list()
        assert len(sleep_calls) == 1

    def test_when_list_called_with_default_config_then_sleep_interval_is_at_least_one_second(
        self,
    ):
        lister, _, sleep_calls = self._make_lister_and_procs()
        lister.list()
        assert sleep_calls[0] >= 1.0

    def test_when_list_called_then_sleep_value_equals_cpu_sample_interval(self):
        cfg = _import_config()
        config = cfg.ProcessConfig()
        lister, _, sleep_calls = self._make_lister_and_procs(config=config)
        lister.list()
        assert sleep_calls[0] == pytest.approx(config.cpu_sample_interval)

    def test_when_custom_cpu_sample_interval_set_then_sleep_receives_that_value(self):
        cfg = _import_config()
        config = cfg.ProcessConfig.from_mapping({"cpu_sample_interval": 2.5})
        lister, _, sleep_calls = self._make_lister_and_procs(config=config)
        lister.list()
        assert sleep_calls[0] == pytest.approx(2.5)

    def test_when_50_procs_are_listed_then_sleep_is_still_called_exactly_once(self):
        """Two-pass: O(1) sleep regardless of proc count — not per-process blocking."""
        PsutilProcessLister = _import_psutil_process_lister()
        procs = [_FakePsutilProc(pid=i + 1) for i in range(50)]
        sleep_calls: list[float] = []
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter(procs),
            sleep=lambda t: sleep_calls.append(t),
        )
        lister.list()
        assert len(sleep_calls) == 1

    def test_when_list_called_then_each_proc_is_sampled_twice_with_none_interval(self):
        """Two-pass: prime (interval=None → 0.0) then read (interval=None → delta)."""
        PsutilProcessLister = _import_psutil_process_lister()
        proc = _FakePsutilProc(pid=1, cpu_pct=5.0)
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter([proc]),
            sleep=lambda t: None,
        )
        lister.list()
        assert len(proc.cpu_percent_calls) == 2
        assert all(c is None for c in proc.cpu_percent_calls)


# ---------------------------------------------------------------------------
# Criterion 3: IoregHidIdleReader.read() converts nanoseconds → seconds
# ---------------------------------------------------------------------------


class TestIoregHidIdleReader:
    """Criterion: read() -> float; parses HIDIdleTime nanoseconds → seconds."""

    def _make_reader(self, hid_ns: int):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        return IoregHidIdleReader(ioreg_runner=_make_ioreg_callable(hid_ns))

    def test_when_hid_idle_time_is_5_billion_ns_then_5_seconds_is_returned(self):
        assert self._make_reader(5_000_000_000).read() == pytest.approx(5.0)

    def test_when_hid_idle_time_is_1_billion_ns_then_1_second_is_returned(self):
        assert self._make_reader(1_000_000_000).read() == pytest.approx(1.0)

    def test_when_hid_idle_time_is_zero_ns_then_zero_seconds_is_returned(self):
        assert self._make_reader(0).read() == pytest.approx(0.0)

    def test_when_hid_idle_time_is_500_million_ns_then_half_second_is_returned(self):
        assert self._make_reader(500_000_000).read() == pytest.approx(0.5)

    def test_when_hid_idle_time_represents_2_hours_then_7200_seconds_is_returned(self):
        assert self._make_reader(7_200 * 1_000_000_000).read() == pytest.approx(7200.0)

    def test_when_read_called_then_return_type_is_float(self):
        assert isinstance(self._make_reader(1_000_000_000).read(), float)


# ---------------------------------------------------------------------------
# Criterion 4: Injectable OS callables; real defaults exist
# ---------------------------------------------------------------------------


class TestInjectionInterface:
    """Criterion: constructors accept injectable callables; no-arg construction works."""

    def test_when_psutil_lister_receives_fake_iter_then_list_returns_empty_tuple(self):
        PsutilProcessLister = _import_psutil_process_lister()
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter([]),
            sleep=lambda t: None,
        )
        assert lister.list() == ()

    def test_when_ioreg_reader_receives_fake_runner_then_read_returns_correct_seconds(
        self,
    ):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        reader = IoregHidIdleReader(ioreg_runner=_make_ioreg_callable(2_000_000_000))
        assert reader.read() == pytest.approx(2.0)

    def test_when_psutil_lister_constructed_without_injection_then_no_error_raised(
        self,
    ):
        PsutilProcessLister = _import_psutil_process_lister()
        PsutilProcessLister()  # real default must not raise at construction time

    def test_when_ioreg_reader_constructed_without_injection_then_no_error_raised(self):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        IoregHidIdleReader()  # real default must not raise at construction time


# ---------------------------------------------------------------------------
# Criterion 5: Fail-safe — garbage/bad data handled without crash
# ---------------------------------------------------------------------------


class TestFailSafe:
    """Criterion: garbage OS output → 0.0 or typed error; bad proc rows skipped."""

    def test_when_ioreg_returns_garbage_then_read_returns_zero_or_raises_typed_error(
        self,
    ):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        reader = IoregHidIdleReader(ioreg_runner=_make_garbage_ioreg_callable())
        try:
            result = reader.read()
            assert result == pytest.approx(0.0)
        except Exception as exc:
            assert not isinstance(exc, (TypeError, AttributeError, KeyError)), (
                f"Garbage ioreg raised a raw built-in error ({type(exc).__name__})"
            )

    def test_when_ioreg_returns_empty_string_then_read_does_not_crash_raw(self):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        reader = IoregHidIdleReader(ioreg_runner=_make_empty_ioreg_callable())
        try:
            result = reader.read()
            assert result == pytest.approx(0.0)
        except Exception as exc:
            assert not isinstance(exc, (TypeError, AttributeError, KeyError)), (
                f"Empty ioreg raised a raw built-in error ({type(exc).__name__})"
            )

    def test_when_process_list_has_one_bad_proc_then_good_procs_are_still_returned(
        self,
    ):
        PsutilProcessLister = _import_psutil_process_lister()
        good_a = _FakePsutilProc(pid=1)
        bad = _RaisingProc(pid=999)
        good_b = _FakePsutilProc(pid=2)
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter([good_a, bad, good_b]),
            sleep=lambda t: None,
        )
        result = lister.list()
        pids = {p.pid for p in result}
        assert 1 in pids
        assert 2 in pids
        assert 999 not in pids

    def test_when_all_procs_are_bad_then_empty_tuple_is_returned_without_raising(self):
        PsutilProcessLister = _import_psutil_process_lister()
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter([_RaisingProc(pid=i) for i in range(5)]),
            sleep=lambda t: None,
        )
        result = lister.list()
        assert isinstance(result, tuple)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------


class TestIoregNsToSecondsConversionProperty:
    """Invariant: result == hid_ns / 1_000_000_000 for all valid ns values."""

    @given(st.integers(min_value=0, max_value=10**18))
    def test_when_valid_hid_ns_given_then_result_equals_ns_divided_by_billion(
        self, hid_ns: int
    ):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        reader = IoregHidIdleReader(ioreg_runner=_make_ioreg_callable(hid_ns))
        assert reader.read() == pytest.approx(hid_ns / 1_000_000_000, abs=1e-6)

    @given(st.integers(min_value=0, max_value=10**18))
    def test_when_valid_hid_ns_given_then_result_is_nonnegative(self, hid_ns: int):
        IoregHidIdleReader = _import_ioreg_hid_reader()
        reader = IoregHidIdleReader(ioreg_runner=_make_ioreg_callable(hid_ns))
        assert reader.read() >= 0.0


class TestPsutilListerOutputTypeProperty:
    """Invariant: list() always returns a tuple; count never exceeds input count."""

    @given(
        st.lists(st.integers(min_value=1, max_value=99_999), min_size=0, max_size=20)
    )
    def test_when_given_any_number_of_procs_then_list_returns_a_tuple(
        self, pids: list[int]
    ):
        PsutilProcessLister = _import_psutil_process_lister()
        procs = [_FakePsutilProc(pid=pid) for pid in pids]
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter(procs),
            sleep=lambda t: None,
        )
        assert isinstance(lister.list(), tuple)

    @given(
        st.lists(st.integers(min_value=1, max_value=99_999), min_size=1, max_size=20)
    )
    def test_when_given_valid_procs_then_result_count_does_not_exceed_input_count(
        self, pids: list[int]
    ):
        PsutilProcessLister = _import_psutil_process_lister()
        procs = [_FakePsutilProc(pid=pid) for pid in pids]
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter(procs),
            sleep=lambda t: None,
        )
        assert len(lister.list()) <= len(procs)


class TestCpuSamplingWindowProperty:
    """Invariant: sleep is always called exactly once with the configured interval."""

    @given(
        st.floats(min_value=1.0, max_value=60.0, allow_nan=False, allow_infinity=False)
    )
    def test_when_any_valid_cpu_sample_interval_then_sleep_called_once_with_that_value(
        self, interval: float
    ):
        cfg = _import_config()
        PsutilProcessLister = _import_psutil_process_lister()
        config = cfg.ProcessConfig.from_mapping({"cpu_sample_interval": interval})
        sleep_calls: list[float] = []
        proc = _FakePsutilProc(pid=1)
        lister = PsutilProcessLister(
            process_iter=lambda *a, **kw: iter([proc]),
            sleep=lambda t: sleep_calls.append(t),
            config=config,
        )
        lister.list()
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(interval)
        assert sleep_calls[0] > 0
