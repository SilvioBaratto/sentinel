"""
Source-blind example tests for DefaultContainerIdleDetector — issue #16.

All tests are derived exclusively from acceptance criteria and requirements.md.
No implementation source was read.  These imports define the contract the
implementation must satisfy; if an import fails, the Red phase is live.

Skipped criteria (oracle: NOT VERIFIABLE):
  - Consecutive-idle counter reset on single non-idle poll (criterion 6)
  - ≥2h measured via FakeClock described as its own criterion (criterion 7)
    — the 2h gate is covered inside criterion-5 tests below.
  - Suite gate and code-quality prose (criteria 10-11)
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from sentinel.config import SystemState
from sentinel.docker.idle_detector import DefaultContainerIdleDetector
from sentinel.domain.protocols import ContainerIdleDetector
from sentinel.domain.value_objects import ContainerCandidate


# ---------------------------------------------------------------------------
# Fakes derived from acceptance-criteria text
# ---------------------------------------------------------------------------


class FakeStatsReader:
    """
    Call-counting double.  Spy-asserts can check call_count == 0 to confirm
    the detector never touched the reader (used for the NORMAL-state test).
    """

    def __init__(self, containers: list[dict]) -> None:
        self._data: dict[str, dict] = {c["name"]: c for c in containers}
        self.call_count: int = 0

    def list_containers(self) -> list[str]:
        self.call_count += 1
        return list(self._data)

    def get_stats(self, name: str) -> dict:
        self.call_count += 1
        return self._data[name]


class FakeClock:
    """
    Injectable clock.  advance() moves the logical time forward without touching
    wall-clock time; used to exercise the ≥2h idle-since gate.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------


def _idle_stats(
    name: str,
    *,
    cpu: float = 0.1,
    exec_active: bool = False,
) -> dict:
    """Return a fully-idle container stats dict (cpu < 0.5%, I/O deltas = 0)."""
    return {
        "name": name,
        "cpu_percent": cpu,
        "net_io_delta": 0,
        "block_io_delta": 0,
        "exec_active": exec_active,
    }


def _make_detector(
    containers: list[dict],
    clock: FakeClock,
    n_polls: int = 3,
) -> tuple[DefaultContainerIdleDetector, FakeStatsReader]:
    reader = FakeStatsReader(containers)
    detector = DefaultContainerIdleDetector(reader=reader, clock=clock, n_polls=n_polls)
    return detector, reader


def _poll_n(
    detector: DefaultContainerIdleDetector,
    state: SystemState,
    n: int,
) -> tuple:
    """Drive n detect() calls; return the last result."""
    result: tuple = ()
    for _ in range(n):
        result = detector.detect(state)
    return result


# ---------------------------------------------------------------------------
# Criterion 1 — DefaultContainerIdleDetector implements ContainerIdleDetector
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_when_instantiated_then_detector_satisfies_ContainerIdleDetector_protocol(
        self,
    ):
        """isinstance check covers structural Protocol compliance."""
        clock = FakeClock()
        detector, _ = _make_detector([], clock)
        assert isinstance(detector, ContainerIdleDetector)

    def test_when_detect_is_called_in_warn_state_then_result_is_a_tuple(self):
        """Return type must be tuple, not list or generator."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api")], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# Criterion 2 — state == NORMAL → () with zero reader calls
# ---------------------------------------------------------------------------


class TestNormalStateShortCircuit:
    def test_when_state_is_NORMAL_then_detect_returns_empty_tuple(self):
        clock = FakeClock()
        detector, _ = _make_detector([_idle_stats("clipcraft_api")], clock)
        result = detector.detect(SystemState.NORMAL)
        assert result == ()

    def test_when_state_is_NORMAL_then_reader_is_not_called_at_all(self):
        """NORMAL must short-circuit; no list_containers or get_stats calls allowed."""
        clock = FakeClock()
        detector, reader = _make_detector([_idle_stats("clipcraft_api")], clock)
        detector.detect(SystemState.NORMAL)
        assert reader.call_count == 0


# ---------------------------------------------------------------------------
# Criterion 3 — always-up containers (optimizer_* + *_db) never emitted
# ---------------------------------------------------------------------------


class TestAlwaysUpContainersNeverEmitted:
    def test_when_optimizer_api_is_idle_then_it_is_not_a_candidate(self):
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("optimizer_api")], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "optimizer_api" for c in result)

    def test_when_optimizer_frontend_is_idle_then_it_is_not_a_candidate(self):
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("optimizer_frontend")], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "optimizer_frontend" for c in result)

    def test_when_db_suffixed_container_is_idle_then_it_is_not_a_candidate(self):
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("postgres_db")], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "postgres_db" for c in result)

    def test_when_optimizer_db_is_idle_then_it_is_not_a_candidate(self):
        """optimizer_db matches both patterns; must still be excluded."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("optimizer_db")], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "optimizer_db" for c in result)

    # --- property-based: always-up invariant holds for ALL matching names ---

    @given(
        suffix=st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="_",
            ),
            min_size=1,
            max_size=30,
        )
    )
    def test_when_any_optimizer_prefixed_name_is_idle_then_it_is_never_emitted(
        self, suffix: str
    ) -> None:
        """Invariant: ∀ suffix, optimizer_{suffix} is never a candidate."""
        name = f"optimizer_{suffix}"
        clock = FakeClock(start=0.0)
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats(name)], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != name for c in result)

    @given(
        prefix=st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="_",
            ),
            min_size=1,
            max_size=30,
        )
    )
    def test_when_any_db_suffixed_name_is_idle_then_it_is_never_emitted(
        self, prefix: str
    ) -> None:
        """Invariant: ∀ prefix, {prefix}_db is never a candidate."""
        name = f"{prefix}_db"
        clock = FakeClock(start=0.0)
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats(name)], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != name for c in result)


# ---------------------------------------------------------------------------
# Criterion 4 — sticky in-use: exec/attach session overrides all other signals
# ---------------------------------------------------------------------------


class TestStickyInUse:
    def test_when_exec_active_then_container_is_not_emitted_regardless_of_cpu(self):
        """exec_active=True with low CPU → still sticky in-use, never emitted."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector(
            [_idle_stats("clipcraft_api", cpu=0.0, exec_active=True)],
            clock,
        )
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_exec_active_over_many_polls_then_container_is_still_not_emitted(self):
        """Sticky in-use persists regardless of poll count or elapsed time."""
        clock = FakeClock()
        clock.advance(6 * 3600)
        detector, _ = _make_detector(
            [_idle_stats("clipcraft_adminer", cpu=0.0, exec_active=True)],
            clock,
        )
        result = _poll_n(detector, SystemState.WARN, 6)
        assert all(c.name != "clipcraft_adminer" for c in result)


# ---------------------------------------------------------------------------
# Criterion 5 — idle gate: CPU<0.5% AND I/O≈0 over N=3 polls AND idle≥2h
# ---------------------------------------------------------------------------


class TestIdleGate:
    def test_when_all_conditions_met_then_container_is_emitted(self):
        """Golden-path: CPU 0.1%, net/block delta 0, N=3, elapsed > 2h → candidate.

        Clock is advanced AFTER the first idle poll so idle_since is anchored at
        T=0; elapsed is then 2h+1min at poll 3, satisfying the 2h gate.
        """
        clock = FakeClock(start=0.0)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        detector.detect(SystemState.WARN)        # poll 1: anchors idle_since = 0
        clock.advance(2 * 3600 + 60)            # 2h 1min → now = 7260
        detector.detect(SystemState.WARN)        # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3: emitted
        assert any(c.name == "clipcraft_api" for c in result)

    def test_when_cpu_exactly_at_threshold_then_container_is_not_emitted(self):
        """CPU = 0.5% is not idle; spec says strictly < 0.5%."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.5)], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_cpu_above_threshold_then_container_is_not_emitted(self):
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=1.0)], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_net_io_delta_is_nonzero_then_container_is_not_emitted(self):
        """Non-zero net I/O delta → I/O condition not met → not idle."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        stats = {
            "name": "clipcraft_api",
            "cpu_percent": 0.1,
            "net_io_delta": 1024,
            "block_io_delta": 0,
            "exec_active": False,
        }
        detector, _ = _make_detector([stats], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_block_io_delta_is_nonzero_then_container_is_not_emitted(self):
        """Non-zero block I/O delta → I/O condition not met → not idle."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        stats = {
            "name": "clipcraft_api",
            "cpu_percent": 0.1,
            "net_io_delta": 0,
            "block_io_delta": 4096,
            "exec_active": False,
        }
        detector, _ = _make_detector([stats], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_only_one_poll_completed_then_not_yet_a_candidate(self):
        """1 of 3 required consecutive idle polls → not yet eligible."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        result = detector.detect(SystemState.WARN)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_only_two_polls_completed_then_not_yet_a_candidate(self):
        """2 of 3 required consecutive idle polls → still not eligible."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        result = _poll_n(detector, SystemState.WARN, 2)
        assert all(c.name != "clipcraft_api" for c in result)

    def test_when_elapsed_time_is_less_than_2h_then_not_a_candidate(self):
        """N polls satisfied but only 1h elapsed since idle-since → not yet a candidate.

        Poll 1 anchors idle_since at T=0; clock advances 1h before polls 2 and 3.
        At poll 3 the consecutive gate (>=3) is satisfied but elapsed = 1h < 2h.
        """
        clock = FakeClock(start=0.0)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        detector.detect(SystemState.WARN)        # poll 1: anchors idle_since = 0
        clock.advance(1 * 3600)                  # advance only 1h
        detector.detect(SystemState.WARN)        # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3: consecutive=3, elapsed<2h
        assert all(c.name != "clipcraft_api" for c in result)

    # --- property-based: poll-count invariant ---

    @given(polls=st.integers(min_value=1, max_value=2))
    def test_when_fewer_than_n_polls_then_container_is_never_a_candidate(
        self, polls: int
    ) -> None:
        """Invariant: for all M < N=3, M consecutive idle polls never emit a candidate."""
        clock = FakeClock(start=0.0)
        clock.advance(3 * 3600)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        result = _poll_n(detector, SystemState.WARN, polls)
        assert all(c.name != "clipcraft_api" for c in result)


# ---------------------------------------------------------------------------
# Issue #18 — idle_since anchoring fixes
# ---------------------------------------------------------------------------


class TestIdleSinceAnchoring:
    def test_when_clock_has_large_start_and_no_time_advances_between_polls_then_not_emitted(
        self,
    ):
        """Regression (issue #18): monotonic origin must not bypass the 2h gate.

        With FakeClock(start=1_000_000) and no advance between polls,
        now - idle_since must equal 0 (not 1_000_000), so the 2h gate is not
        satisfied after only N=3 consecutive idle polls.
        """
        clock = FakeClock(start=1_000_000)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        result = _poll_n(detector, SystemState.WARN, 3)
        assert all(c.name != "clipcraft_api" for c in result), (
            "container was emitted immediately — monotonic-origin bug not fixed"
        )

    def test_when_first_idle_poll_at_T_then_advance_2h_then_complete_N_polls_then_emitted(
        self,
    ):
        """idle_since anchored at first idle poll; advance >= 2h → candidate emitted."""
        clock = FakeClock(start=0.0)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        detector.detect(SystemState.WARN)        # poll 1: anchors idle_since = 0
        clock.advance(2 * 3600 + 1)             # advance 2h+1s
        detector.detect(SystemState.WARN)        # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3: should emit
        assert any(c.name == "clipcraft_api" for c in result)

    def test_when_first_idle_at_T_and_advance_2h_then_idle_seconds_reflects_elapsed(
        self,
    ):
        """Reported idle_seconds must be clock.now() - first_idle_observation."""
        clock = FakeClock(start=0.0)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        detector.detect(SystemState.WARN)        # poll 1: idle_since = 0
        clock.advance(2 * 3600 + 60)            # 2h 1min → now = 7260
        detector.detect(SystemState.WARN)        # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3
        candidate = next(c for c in result if c.name == "clipcraft_api")
        # idle_seconds must reflect elapsed time, not the monotonic origin
        assert candidate.idle_seconds >= 2 * 3600
        assert candidate.idle_seconds < 3 * 3600  # not a wild large value

    def test_when_busy_poll_then_both_counter_and_idle_since_are_reset(self):
        """A busy poll resets consecutive counter AND idle-since anchor.

        After a busy poll, a fresh idle streak must wait the full 2h again;
        it must not inherit the elapsed time from the previous streak.
        """
        busy = {
            "name": "clipcraft_api",
            "cpu_percent": 5.0,
            "net_io_delta": 0,
            "block_io_delta": 0,
            "exec_active": False,
        }
        idle_s = _idle_stats("clipcraft_api", cpu=0.1)

        class _ScriptedReader:
            _steps = [idle_s, idle_s, busy, idle_s, idle_s, idle_s]
            _i = 0

            def list_containers(self) -> list[str]:
                return ["clipcraft_api"]

            def get_stats(self, name: str) -> dict:
                s = self._steps[self._i]
                self._i += 1
                return s

        clock = FakeClock(start=0.0)
        detector = DefaultContainerIdleDetector(
            reader=_ScriptedReader(), clock=clock, n_polls=3
        )
        # Two idle polls then a busy poll (resets streak)
        detector.detect(SystemState.WARN)  # idle 1
        clock.advance(3600)
        detector.detect(SystemState.WARN)  # idle 2
        clock.advance(3600)
        detector.detect(SystemState.WARN)  # busy → reset
        # After reset, one more idle poll at T=7200; idle-since re-anchored here
        clock.advance(1)
        detector.detect(SystemState.WARN)  # idle 1 of new streak, idle_since = 7201
        clock.advance(3600)
        detector.detect(SystemState.WARN)  # idle 2 of new streak
        # Only 3600s elapsed since reset anchor — still < 2h, not emittable
        result = detector.detect(SystemState.WARN)  # idle 3 of new streak
        assert all(c.name != "clipcraft_api" for c in result), (
            "container emitted too soon after busy-poll reset"
        )


# ---------------------------------------------------------------------------
# Criterion 8 — ContainerCandidate.reason is populated
# ---------------------------------------------------------------------------


class TestCandidateReason:
    def _emit_candidate(self) -> ContainerCandidate:
        clock = FakeClock(start=0.0)
        detector, _ = _make_detector([_idle_stats("clipcraft_api", cpu=0.1)], clock)
        detector.detect(SystemState.WARN)        # poll 1: anchors idle_since
        clock.advance(3 * 3600)                  # advance 3h past anchor
        detector.detect(SystemState.WARN)        # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3
        assert len(result) >= 1, "pre-condition: candidate must be emitted"
        return next(c for c in result if c.name == "clipcraft_api")

    def test_when_candidate_is_emitted_then_reason_is_a_non_empty_string(self):
        candidate = self._emit_candidate()
        assert isinstance(candidate.reason, str)
        assert candidate.reason.strip() != ""

    def test_when_candidate_is_emitted_then_reason_contains_container_name(self):
        """Spec example: 'clipcraft_api idle 2h, cpu 0.1%, …' → name must appear."""
        candidate = self._emit_candidate()
        assert "clipcraft_api" in candidate.reason

    def test_when_candidate_is_emitted_then_reason_mentions_cpu(self):
        """Spec example includes CPU figure; reason must reference it."""
        candidate = self._emit_candidate()
        assert "cpu" in candidate.reason.lower() or "%" in candidate.reason


# ---------------------------------------------------------------------------
# Criterion 9 — never raises into the pipeline on reader failure
# ---------------------------------------------------------------------------


class _TotallyBrokenReader:
    """Simulates docker daemon being completely unavailable."""

    call_count = 0

    def list_containers(self) -> list[str]:
        self.call_count += 1
        raise RuntimeError("docker daemon unavailable")

    def get_stats(self, name: str) -> dict:
        raise RuntimeError("docker daemon unavailable")


class _PartiallyBrokenReader:
    """Returns valid stats for good containers; raises for one specific container."""

    def __init__(self, good_containers: list[dict], broken_name: str) -> None:
        self._good: dict[str, dict] = {c["name"]: c for c in good_containers}
        self._broken = broken_name

    def list_containers(self) -> list[str]:
        return list(self._good) + [self._broken]

    def get_stats(self, name: str) -> dict:
        if name == self._broken:
            raise RuntimeError("stats unavailable for this container")
        return self._good[name]


class TestNeverRaisesOnReaderFailure:
    def test_when_reader_raises_on_list_then_detect_returns_tuple_without_raising(self):
        """Total reader failure → detect() returns a tuple, never propagates the error."""
        clock = FakeClock()
        clock.advance(3 * 3600)
        reader = _TotallyBrokenReader()
        detector = DefaultContainerIdleDetector(reader=reader, clock=clock, n_polls=3)
        result = detector.detect(SystemState.WARN)
        assert isinstance(result, tuple)

    def test_when_reader_raises_repeatedly_then_detect_stays_safe(self):
        """Reader failure on every poll — detector must remain callable."""
        clock = FakeClock()
        reader = _TotallyBrokenReader()
        detector = DefaultContainerIdleDetector(reader=reader, clock=clock, n_polls=3)
        for _ in range(3):
            result = detector.detect(SystemState.WARN)
            assert isinstance(result, tuple)

    def test_when_one_container_stats_raises_then_other_candidates_still_returned(self):
        """
        Partial reader failure: a healthy container must still surface as a
        candidate even when another container's get_stats() raises.

        Clock is advanced after the first poll so idle_since is anchored correctly
        and the 2h gate is satisfied by poll 3.
        """
        clock = FakeClock(start=0.0)
        reader = _PartiallyBrokenReader(
            good_containers=[_idle_stats("clipcraft_api", cpu=0.1)],
            broken_name="broken_container",
        )
        detector = DefaultContainerIdleDetector(reader=reader, clock=clock, n_polls=3)
        detector.detect(SystemState.WARN)    # poll 1: anchors idle_since
        clock.advance(3 * 3600)             # advance 3h past anchor
        detector.detect(SystemState.WARN)    # poll 2
        result = detector.detect(SystemState.WARN)  # poll 3: emitted
        assert isinstance(result, tuple)
        assert any(c.name == "clipcraft_api" for c in result)
