"""Source-blind example tests for issue #14.

Tests for DefaultProcessIdleDetector.detect() — per-process idle detection.
Authored directly from acceptance criteria; no implementation source was read.
All tests are in Red phase: they fail until the implementation is complete.

Design assumption: ProcessInfo carries an `idle_seconds` field (float) representing
per-process idle time as reported by the lister/tracker. This is the simplest design
consistent with criterion "max(proc_idle, hid_idle) >= idle_seconds" — the lister
computes how long each process's CPU has been below threshold and embeds it in the info
object, letting the detector stay small (< 50 lines per SOLID constraint).
"""

from __future__ import annotations

from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Import helpers — paths inferred from criteria + project conventions only
# ---------------------------------------------------------------------------


def _vo():
    from sentinel.domain import value_objects

    return value_objects


def _cfg():
    import sentinel.config as c

    return c


def _detector_class():
    from sentinel.process.idle_detector import DefaultProcessIdleDetector

    return DefaultProcessIdleDetector


def _state():
    from sentinel.domain.value_objects import SentinelState

    return SentinelState


# ---------------------------------------------------------------------------
# Constants matching acceptance-criteria defaults
# ---------------------------------------------------------------------------

_TWO_HOURS: float = 7200.0
_IDLE_CPU: float = 1.0  # ProcessConfig.idle_cpu_percent default


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_info(
    pid: int = 1,
    name: str = "Chrome",
    cpu_percent: float = 0.2,
    idle_seconds: float = _TWO_HOURS + 1.0,
) -> object:  # type: ignore[return-value]
    """Build a ProcessInfo. idle_seconds = per-process idle time (assumed field)."""
    vo = _vo()
    return vo.ProcessInfo(
        pid=pid,
        ppid=0,
        name=name,
        cmdline=(name,),
        has_tty=False,
        tty=None,
        pgid=None,
        cpu_percent=cpu_percent,
        rss_bytes=0,
        create_time=None,
        idle_seconds=idle_seconds,
    )


def _reapable(pid: int = 1, name: str = "Chrome") -> object:  # type: ignore[return-value]
    vo = _vo()
    return vo.ProcessClassification(
        pid=pid,
        name=name,
        protection=vo.ProcessProtection.REAPABLE,
        reason="gui-idle",
    )


def _protected(pid: int = 1, name: str = "Terminal") -> object:  # type: ignore[return-value]
    vo = _vo()
    return vo.ProcessClassification(
        pid=pid,
        name=name,
        protection=vo.ProcessProtection.PROTECTED,
        reason="tty",
    )


def _not_frontmost(other_pid: int = 99) -> object:  # type: ignore[return-value]
    vo = _vo()
    return vo.FrontmostApp(bundle_id=None, name="Finder", pid=other_pid)


def _is_frontmost(pid: int = 1, name: str = "Chrome") -> object:  # type: ignore[return-value]
    vo = _vo()
    return vo.FrontmostApp(bundle_id=None, name=name, pid=pid)


class _CountingLister:
    def __init__(self, processes: tuple = ()):
        self.calls = 0
        self._procs = processes

    def list(self) -> tuple:
        self.calls += 1
        return self._procs


class _CountingFrontmostReader:
    def __init__(self, app: object = None):
        self.calls = 0
        self._app = app

    def read(self):  # type: ignore[override]
        self.calls += 1
        return self._app


class _CountingHidReader:
    def __init__(self, seconds: float = _TWO_HOURS + 1.0):
        self.calls = 0
        self._seconds = seconds

    def read(self) -> float:
        self.calls += 1
        return self._seconds


class _DictClassifier:
    """Returns a pre-configured classification per pid; PROTECTED for unknown pids."""

    def __init__(self, by_pid: dict):
        self._by_pid = by_pid
        self.received_indices: list = []

    def classify(self, proc: object, index: object):  # type: ignore[override]
        self.received_indices.append(index)
        return self._by_pid.get(proc.pid, _protected(proc.pid, proc.name))  # type: ignore[attr-defined]


class _RaisingClassifier:
    """Simulates a classifier that always raises (any reader failure)."""

    def classify(self, proc: object, index: object):  # type: ignore[override]
        raise RuntimeError("simulated reader failure")


class _PartialRaisingClassifier:
    """Raises only for the configured pid; returns REAPABLE for all others."""

    def __init__(self, bad_pid: int):
        self._bad_pid = bad_pid

    def classify(self, proc: object, index: object):  # type: ignore[override]
        if proc.pid == self._bad_pid:  # type: ignore[attr-defined]
            raise RuntimeError("simulated failure for pid=%d" % proc.pid)  # type: ignore[attr-defined]
        vo = _vo()
        return vo.ProcessClassification(
            pid=proc.pid,  # type: ignore[attr-defined]
            name=proc.name,  # type: ignore[attr-defined]
            protection=vo.ProcessProtection.REAPABLE,
            reason="idle",
        )


# ---------------------------------------------------------------------------
# Detector factory
# ---------------------------------------------------------------------------


def _make_detector(
    lister=None,
    frontmost_reader=None,
    hid_reader=None,
    classifier=None,
    config=None,
) -> object:  # type: ignore[return-value]
    cls = _detector_class()
    cfg = config or _cfg().ProcessConfig()
    return cls(
        lister=lister or _CountingLister(),
        frontmost_reader=frontmost_reader or _CountingFrontmostReader(_not_frontmost()),
        hid_reader=hid_reader or _CountingHidReader(),
        classifier=classifier or _DictClassifier({}),
        config=cfg,
    )


def _warn() -> object:
    return _state().WARN


def _normal() -> object:
    return _state().NORMAL


# ---------------------------------------------------------------------------
# Criterion 1 — DefaultProcessIdleDetector implements ProcessIdleDetector
# ---------------------------------------------------------------------------


class TestDefaultProcessIdleDetectorInterface:
    def test_when_detector_instantiated_then_it_is_a_process_idle_detector(self):
        from sentinel.domain.protocols import ProcessIdleDetector

        detector = _make_detector()
        assert isinstance(detector, ProcessIdleDetector)

    def test_when_detect_called_in_warn_state_then_result_is_tuple(self):
        detector = _make_detector()
        result = detector.detect(_warn())
        assert isinstance(result, tuple)

    def test_when_detect_called_with_warn_state_then_result_contains_process_candidates(
        self,
    ):
        """Each element in the tuple must be a ProcessCandidate."""
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost())
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        vo = _vo()
        for item in result:
            assert isinstance(item, vo.ProcessCandidate)


# ---------------------------------------------------------------------------
# Criterion 2 — state == NORMAL → () and no reader called
# ---------------------------------------------------------------------------


class TestNormalStateShortCircuit:
    def test_when_state_is_normal_then_detect_returns_empty_tuple(self):
        detector = _make_detector()
        assert detector.detect(_normal()) == ()

    def test_when_state_is_normal_then_lister_is_not_called(self):
        lister = _CountingLister((_make_info(),))
        detector = _make_detector(lister=lister)
        detector.detect(_normal())
        assert lister.calls == 0

    def test_when_state_is_normal_then_frontmost_reader_is_not_called(self):
        frontmost = _CountingFrontmostReader(_not_frontmost())
        detector = _make_detector(frontmost_reader=frontmost)
        detector.detect(_normal())
        assert frontmost.calls == 0

    def test_when_state_is_normal_then_hid_reader_is_not_called(self):
        hid = _CountingHidReader()
        detector = _make_detector(hid_reader=hid)
        detector.detect(_normal())
        assert hid.calls == 0


# ---------------------------------------------------------------------------
# Criterion 3 — ProcessIndex built once, same object passed to every classify call
# ---------------------------------------------------------------------------


class TestProcessIndexBuiltOnce:
    def test_when_detect_called_then_lister_is_called_exactly_once(self):
        proc = _make_info(pid=1)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        detector = _make_detector(lister=lister, classifier=classifier)
        detector.detect(_warn())
        assert lister.calls == 1

    def test_when_two_processes_listed_then_classifier_receives_same_index_for_both(
        self,
    ):
        procs = (
            _make_info(pid=1, name="Chrome"),
            _make_info(pid=2, name="Slack"),
        )
        classifier = _DictClassifier(
            {
                1: _reapable(1, "Chrome"),
                2: _reapable(2, "Slack"),
            }
        )
        lister = _CountingLister(procs)
        detector = _make_detector(lister=lister, classifier=classifier)
        detector.detect(_warn())
        indices = classifier.received_indices
        assert len(indices) == 2
        assert indices[0] is indices[1]


# ---------------------------------------------------------------------------
# Criterion 4 — emits candidate only when all four conditions hold
# ---------------------------------------------------------------------------


class TestCandidateEmissionConditions:
    def test_when_all_conditions_met_then_exactly_one_candidate_emitted(self):
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1
        assert result[0].info.pid == 1

    def test_when_process_is_protected_then_no_candidate_emitted(self):
        proc = _make_info(
            pid=1, name="Terminal", cpu_percent=0.0, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _protected(1, "Terminal")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_candidate_emitted_then_idle_seconds_is_max_of_proc_and_hid_idle(self):
        """Criterion: effective idle = max(proc_idle, hid_idle)."""
        proc_idle = _TWO_HOURS + 100.0
        hid_idle = _TWO_HOURS + 500.0
        proc = _make_info(pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=proc_idle)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(hid_idle)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1
        assert result[0].idle_seconds == max(proc_idle, hid_idle)


# ---------------------------------------------------------------------------
# Criterion 5 — frontmost app excluded even when otherwise idle
# ---------------------------------------------------------------------------


class TestFrontmostExclusion:
    def test_when_process_is_frontmost_then_excluded_despite_low_cpu_and_long_idle(
        self,
    ):
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.0, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_is_frontmost(pid=1, name="Chrome"))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_frontmost_is_different_pid_then_idle_process_is_included(self):
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_is_frontmost(pid=2, name="Finder"))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Criterion 6 — CPU at/above threshold excluded; idle < 2h excluded
# ---------------------------------------------------------------------------


class TestThresholdExclusions:
    def test_when_cpu_equals_threshold_then_process_excluded(self):
        """cpu_percent == idle_cpu_percent is NOT below threshold."""
        proc = _make_info(pid=1, cpu_percent=1.0, idle_seconds=_TWO_HOURS + 1)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_cpu_above_threshold_then_process_excluded(self):
        proc = _make_info(pid=1, cpu_percent=5.0, idle_seconds=_TWO_HOURS + 1)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_effective_idle_exactly_two_hours_then_process_excluded(self):
        """Criterion: STRICTLY less than 2h — boundary is excluded (<, not <=)."""
        proc = _make_info(pid=1, cpu_percent=0.2, idle_seconds=_TWO_HOURS)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(_TWO_HOURS)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_idle_under_two_hours_then_process_excluded(self):
        """max(proc_idle=1h, hid_idle=1h) < 2h → excluded."""
        proc = _make_info(pid=1, cpu_percent=0.2, idle_seconds=3600.0)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(3600.0)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()


# ---------------------------------------------------------------------------
# Criterion 7 — folded HID idle raises effective idle when proc_idle is lower
# ---------------------------------------------------------------------------


class TestHidIdleFolding:
    def test_when_hid_idle_exceeds_2h_but_proc_idle_does_not_then_candidate_emitted(
        self,
    ):
        """max(proc_idle=1h, hid_idle=3h) = 3h >= 2h → emitted."""
        proc = _make_info(pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=3600.0)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1.0)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1

    def test_when_proc_idle_exceeds_2h_but_hid_idle_does_not_then_candidate_emitted(
        self,
    ):
        """max(proc_idle=3h, hid_idle=1h) = 3h >= 2h → emitted."""
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1.0
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(3600.0)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1

    def test_when_both_proc_and_hid_idle_below_2h_then_excluded(self):
        """max(1h, 90min) = 90min < 2h → excluded even though hid > proc."""
        proc = _make_info(pid=1, cpu_percent=0.2, idle_seconds=3600.0)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(5400.0)  # 90 min — still < 2h
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert result == ()

    def test_when_hid_idle_folds_in_then_candidate_idle_seconds_reflects_hid_value(
        self,
    ):
        """Candidate.idle_seconds = max(proc_idle, hid_idle) = hid_idle when hid > proc."""
        proc_idle = 3600.0
        hid_idle = _TWO_HOURS + 999.0
        proc = _make_info(pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=proc_idle)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(hid_idle)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert len(result) == 1
        assert result[0].idle_seconds == hid_idle


# ---------------------------------------------------------------------------
# Criterion 8 — reader raising → protect/drop that process; partial list; no raise
# ---------------------------------------------------------------------------


class TestProtectOnAmbiguity:
    def test_when_classifier_always_raises_then_detect_does_not_raise(self):
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        detector = _make_detector(lister=lister, classifier=_RaisingClassifier())
        result = detector.detect(_warn())
        assert isinstance(result, tuple)

    def test_when_classifier_raises_for_process_then_that_process_is_not_in_result(
        self,
    ):
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        detector = _make_detector(lister=lister, classifier=_RaisingClassifier())
        result = detector.detect(_warn())
        assert all(c.info.pid != 1 for c in result)

    def test_when_one_process_raises_and_another_succeeds_then_partial_list_returned(
        self,
    ):
        """Bad process dropped; good process included — partial list, no raise."""
        proc_bad = _make_info(
            pid=1, name="Chrome", cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        proc_good = _make_info(
            pid=2, name="Slack", cpu_percent=0.1, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc_bad, proc_good))
        classifier = _PartialRaisingClassifier(bad_pid=1)
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        pids = {c.info.pid for c in result}
        assert 1 not in pids
        assert 2 in pids

    def test_when_classifier_raises_for_every_process_then_empty_tuple_returned(self):
        procs = (_make_info(pid=i) for i in range(1, 4))
        lister = _CountingLister(tuple(procs))
        detector = _make_detector(lister=lister, classifier=_RaisingClassifier())
        result = detector.detect(_warn())
        assert result == ()


# ---------------------------------------------------------------------------
# Criterion 9 — reason string populated
# ---------------------------------------------------------------------------


class TestReasonStringPopulated:
    def _setup_idle_detector(self, name: str = "Chrome") -> object:
        proc = _make_info(
            pid=1, name=name, cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, name)})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        return _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )

    def test_when_candidate_emitted_then_reason_is_non_empty_string(self):
        result = self._setup_idle_detector().detect(_warn())
        assert len(result) == 1
        assert isinstance(result[0].reason, str)
        assert result[0].reason.strip() != ""

    def test_when_candidate_emitted_then_reason_contains_process_name(self):
        """Criterion example: 'Chrome idle 3h12m, cpu 0.2%, not frontmost'."""
        result = self._setup_idle_detector(name="Chrome").detect(_warn())
        assert len(result) == 1
        assert "Chrome" in result[0].reason

    def test_when_candidate_emitted_then_reason_mentions_idle(self):
        result = self._setup_idle_detector().detect(_warn())
        assert len(result) == 1
        assert "idle" in result[0].reason.lower()

    def test_when_candidate_emitted_then_reason_mentions_not_frontmost(self):
        """Criterion example ends with 'not frontmost'."""
        result = self._setup_idle_detector().detect(_warn())
        assert len(result) == 1
        assert "frontmost" in result[0].reason.lower()

    def test_when_candidate_emitted_then_reason_mentions_cpu(self):
        result = self._setup_idle_detector().detect(_warn())
        assert len(result) == 1
        assert "cpu" in result[0].reason.lower()


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestNormalStateInvariant:
    @given(
        cpu=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        idle=st.floats(min_value=0.0, max_value=86400.0, allow_nan=False),
    )
    def test_when_state_is_normal_then_always_returns_empty_for_any_process(
        self, cpu: float, idle: float
    ):
        """Invariant: NORMAL → () regardless of any process's CPU or idle values."""
        proc = _make_info(pid=1, cpu_percent=cpu, idle_seconds=idle)
        lister = _CountingLister((proc,))
        detector = _make_detector(lister=lister)
        assert detector.detect(_normal()) == ()


class TestCpuAboveThresholdInvariant:
    @given(cpu=st.floats(min_value=_IDLE_CPU, max_value=100.0, allow_nan=False))
    def test_when_cpu_at_or_above_threshold_then_never_in_candidates(self, cpu: float):
        """Invariant: cpu_percent >= idle_cpu_percent always excludes the process."""
        proc = _make_info(
            pid=1, name="Chrome", cpu_percent=cpu, idle_seconds=_TWO_HOURS + 1
        )
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1, "Chrome")})
        hid = _CountingHidReader(_TWO_HOURS + 1)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert all(c.info.pid != 1 for c in result)


class TestIdleBelowTwoHoursInvariant:
    @given(
        proc_idle=st.floats(min_value=0.0, max_value=_TWO_HOURS - 1.0, allow_nan=False),
        hid_idle=st.floats(min_value=0.0, max_value=_TWO_HOURS - 1.0, allow_nan=False),
    )
    def test_when_both_idle_values_below_2h_then_never_in_candidates(
        self, proc_idle: float, hid_idle: float
    ):
        """Invariant: max(proc_idle, hid_idle) < 2h → always excluded."""
        proc = _make_info(pid=1, cpu_percent=0.2, idle_seconds=proc_idle)
        lister = _CountingLister((proc,))
        classifier = _DictClassifier({1: _reapable(1)})
        hid = _CountingHidReader(hid_idle)
        frontmost = _CountingFrontmostReader(_not_frontmost(99))
        detector = _make_detector(
            lister=lister,
            classifier=classifier,
            hid_reader=hid,
            frontmost_reader=frontmost,
        )
        result = detector.detect(_warn())
        assert all(c.info.pid != 1 for c in result)


class TestProtectOnAmbiguityNeverRaisesInvariant:
    @given(n_procs=st.integers(min_value=0, max_value=10))
    def test_when_classifier_always_raises_then_detect_never_raises_for_any_process_count(
        self, n_procs: int
    ):
        """Invariant: a perpetually-raising classifier never causes detect() to raise."""
        procs = tuple(
            _make_info(pid=i, cpu_percent=0.2, idle_seconds=_TWO_HOURS + 1)
            for i in range(1, n_procs + 1)
        )
        lister = _CountingLister(procs)
        detector = _make_detector(lister=lister, classifier=_RaisingClassifier())
        result = detector.detect(_warn())
        assert isinstance(result, tuple)
