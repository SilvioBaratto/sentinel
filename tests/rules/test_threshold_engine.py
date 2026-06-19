"""Tests for the threshold engine: HysteresisGate and evaluate → CandidateSignal.

Issue #5: feat: threshold engine with hysteresis + cooldown emitting CandidateSignal
Red-phase TDD — authored from acceptance criteria only, never from implementation source.

Assumptions (documented where spec text is ambiguous):
- HysteresisGate and evaluate live in sentinel.rules.threshold.
- HysteresisGate(confirm_samples, cooldown_s, clock) is stateful; each call to
  confirmed(condition, now) advances the internal streak counter and checks cooldown.
  `now` is the float timestamp passed explicitly; `clock` is a zero-arg callable used
  as the canonical time source (tests inject FakeClock and pass clock() as `now`).
- evaluate(history) is a pure function accepting list[ResourceSample] and returning
  CandidateSignal; it applies hysteresis internally without persistent external state.
- SentinelState.WARN is the proposed_state for sustained WARN-pressure histories.
- SentinelState.NORMAL is the proposed_state when all samples show NORMAL pressure
  and disk free is above the 20 GiB floor (requirements.md).
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from sentinel.domain.value_objects import (
    CandidateSignal,
    PressureLevel,
    ResourceSample,
    SentinelState,
)
from sentinel.rules.threshold import HysteresisGate, evaluate

from tests.conftest import make_disk, make_memory, make_sample

# ---------------------------------------------------------------------------
# Constants derived from requirements.md
# ---------------------------------------------------------------------------

_DISK_ABOVE_FLOOR_GiB = 50.0  # comfortably above the 20 GiB floor
_DISK_BELOW_FLOOR_GiB = 19.9  # below the floor (not used in verifiable criteria)


# ---------------------------------------------------------------------------
# FakeClock — injected into HysteresisGate for deterministic cooldown tests
# ---------------------------------------------------------------------------


class FakeClock:
    """Zero-argument callable returning a controllable float timestamp (seconds)."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# ---------------------------------------------------------------------------
# Shared history builder (pressure + disk constant, memory optionally varied)
# ---------------------------------------------------------------------------


def _warn_history(n: int = 5, memory=None) -> list[ResourceSample]:
    """Return n WARN-pressure samples above the disk floor.

    Disk and pressure are held constant so only memory can vary.
    """
    return [
        make_sample(
            timestamp=float(i),
            pressure=PressureLevel.WARN,
            disks=(make_disk(free_gib=_DISK_ABOVE_FLOOR_GiB),),
            memory=memory or make_memory(),
        )
        for i in range(n)
    ]


def _normal_history(n: int = 5) -> list[ResourceSample]:
    """Return n NORMAL-pressure samples well above the disk floor."""
    return [
        make_sample(
            timestamp=float(i),
            pressure=PressureLevel.NORMAL,
            disks=(make_disk(free_gib=_DISK_ABOVE_FLOOR_GiB),),
        )
        for i in range(n)
    ]


# ===========================================================================
# Criterion 1 & 2 — HysteresisGate confirm_samples logic
#
# "confirmed(condition, now) returns True only after the condition holds for
#  confirm_samples consecutive evals AND cooldown elapsed since the last
#  confirmed flip"
# "A single WARN sample after NORMAL does NOT yield WARN;
#  confirm_samples consecutive WARN samples do"
# ===========================================================================


class TestHysteresisGateConfirmSamples:
    def test_when_condition_true_for_fewer_than_confirm_samples_then_not_confirmed(
        self,
    ):
        """confirm_samples=3: two consecutive True evals must NOT open the gate."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=3, cooldown_s=0.0, clock=clock)
        gate.confirmed(True, now=clock())
        result = gate.confirmed(True, now=clock())
        assert result is False

    def test_when_condition_true_for_exactly_confirm_samples_then_confirmed(self):
        """confirm_samples=3: the third consecutive True eval must return True."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=3, cooldown_s=0.0, clock=clock)
        gate.confirmed(True, now=clock())
        gate.confirmed(True, now=clock())
        result = gate.confirmed(True, now=clock())
        assert result is True

    def test_when_single_warn_after_normal_then_not_confirmed(self):
        """A single True eval after a False eval does NOT confirm (criterion 2 first half)."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=2, cooldown_s=0.0, clock=clock)
        gate.confirmed(False, now=clock())  # NORMAL
        result = gate.confirmed(True, now=clock())  # single WARN
        assert result is False

    def test_when_confirm_samples_consecutive_warn_then_confirmed(self):
        """confirm_samples=2 consecutive True evals DO confirm (criterion 2 second half)."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=2, cooldown_s=0.0, clock=clock)
        gate.confirmed(True, now=clock())  # streak: 1
        result = gate.confirmed(True, now=clock())  # streak: 2 → confirms
        assert result is True

    def test_when_false_interrupts_streak_then_consecutive_counter_resets(self):
        """A False eval mid-streak resets the streak; previous partial run is discarded.

        Assumption: any False eval is sufficient to break the streak, so
        the gate does not remember partial runs across a gap.
        """
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=3, cooldown_s=0.0, clock=clock)
        gate.confirmed(True, now=clock())  # streak: 1
        gate.confirmed(False, now=clock())  # break — streak resets
        gate.confirmed(True, now=clock())  # new streak: 1
        result = gate.confirmed(True, now=clock())  # new streak: 2 (< 3)
        assert result is False

    # ---- Property: for any N >= 2, N-1 True evals never confirm ----

    @given(st.integers(min_value=2, max_value=8))
    def test_when_n_minus_1_consecutive_true_evals_then_none_confirms(self, n: int):
        """N-1 consecutive True evals must each return False for any N >= 2."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=n, cooldown_s=0.0, clock=clock)
        results = [gate.confirmed(True, now=clock()) for _ in range(n - 1)]
        assert all(r is False for r in results)

    # ---- Property: for any N >= 1, the Nth consecutive True eval confirms ----

    @given(st.integers(min_value=1, max_value=8))
    def test_when_exactly_n_consecutive_true_evals_then_nth_returns_true(self, n: int):
        """The Nth consecutive True eval must be the first to return True."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=n, cooldown_s=0.0, clock=clock)
        results = [gate.confirmed(True, now=clock()) for _ in range(n)]
        # All but the last must be False; the last must be True
        assert all(r is False for r in results[:-1])
        assert results[-1] is True


# ===========================================================================
# Criterion 4 — HysteresisGate cooldown
#
# "Cooldown blocks a second flip within cooldown_s (injected clock advanced
#  in-test)"
# ===========================================================================


class TestHysteresisGateCooldown:
    def test_when_second_flip_within_cooldown_s_then_blocked(self):
        """A second confirmation attempted before cooldown_s expires returns False."""
        clock = FakeClock(start=0.0)
        cooldown_s = 10.0
        gate = HysteresisGate(confirm_samples=1, cooldown_s=cooldown_s, clock=clock)

        first = gate.confirmed(True, now=clock())
        assert first is True, "sanity: first confirmation must open the gate"

        clock.advance(cooldown_s - 1.0)  # still within cooldown
        second = gate.confirmed(True, now=clock())
        assert second is False

    def test_when_second_flip_after_cooldown_elapsed_then_allowed(self):
        """A second confirmation after cooldown_s has fully elapsed returns True."""
        clock = FakeClock(start=0.0)
        cooldown_s = 10.0
        gate = HysteresisGate(confirm_samples=1, cooldown_s=cooldown_s, clock=clock)

        gate.confirmed(True, now=clock())  # first flip at t=0
        clock.advance(cooldown_s + 0.01)  # just past cooldown
        second = gate.confirmed(True, now=clock())
        assert second is True

    def test_when_cooldown_is_zero_then_consecutive_confirmations_are_all_allowed(self):
        """cooldown_s=0 means no waiting; every satisfied streak immediately confirms."""
        clock = FakeClock(start=0.0)
        gate = HysteresisGate(confirm_samples=1, cooldown_s=0.0, clock=clock)

        first = gate.confirmed(True, now=clock())
        second = gate.confirmed(True, now=clock())
        assert first is True
        assert second is True


# ===========================================================================
# Criterion 6 — evaluate: MemoryReport variation must not change proposed_state
#
# "Varying MemoryReport while pressure/disk held constant never changes
#  proposed_state"
# ===========================================================================


class TestEvaluateMemoryReportInvariance:
    def test_when_only_memory_report_differs_then_proposed_state_is_unchanged(self):
        """Two histories identical in pressure/disk but different in MemoryReport
        must produce the same proposed_state."""
        history_low_mem = _warn_history(memory=make_memory(used_gib=4.0))
        history_high_mem = _warn_history(memory=make_memory(used_gib=14.0))

        signal_low = evaluate(history_low_mem)
        signal_high = evaluate(history_high_mem)

        assert signal_low.proposed_state == signal_high.proposed_state

    # ---- Property: proposed_state is stable under any MemoryReport.used_bytes ----

    @given(
        used_gib=st.floats(
            min_value=0.0,
            max_value=16.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    def test_when_memory_used_varies_with_pressure_and_disk_constant_then_proposed_state_stable(
        self, used_gib: float
    ) -> None:
        """For any MemoryReport.used_bytes, proposed_state depends on pressure and
        disk alone — memory figures are advisory per requirements.md (section 1)."""
        baseline = _warn_history(memory=make_memory(used_gib=8.0))
        varied = _warn_history(memory=make_memory(used_gib=used_gib))

        assert evaluate(baseline).proposed_state == evaluate(varied).proposed_state


# ===========================================================================
# Criterion 7 — evaluate returns CandidateSignal — a proposal, never an action
#
# "evaluate(history) returns CandidateSignal(proposed_state, reason,
#  triggering_sample) — a proposal, never an action"
# ===========================================================================


class TestEvaluateReturnShape:
    def test_when_evaluate_called_then_result_is_candidate_signal(self):
        """evaluate() must return a CandidateSignal instance."""
        result = evaluate(_warn_history())
        assert isinstance(result, CandidateSignal)

    def test_when_evaluate_called_then_proposed_state_is_sentinel_state(self):
        """CandidateSignal.proposed_state must be a SentinelState enum member."""
        result = evaluate(_warn_history())
        assert isinstance(result.proposed_state, SentinelState)

    def test_when_evaluate_called_then_reason_is_non_empty_string(self):
        """CandidateSignal.reason must be a non-empty string explaining the proposal."""
        result = evaluate(_warn_history())
        assert isinstance(result.reason, str)
        assert result.reason != ""

    def test_when_evaluate_called_then_triggering_sample_is_resource_sample(self):
        """CandidateSignal.triggering_sample must be a ResourceSample instance."""
        result = evaluate(_warn_history())
        assert isinstance(result.triggering_sample, ResourceSample)

    def test_when_evaluate_called_with_normal_history_then_proposed_state_is_normal(
        self,
    ):
        """A history of NORMAL-pressure samples above the disk floor must propose NORMAL.

        Assumption: evaluate never proposes WARN/CRITICAL/DISK-LOW when all
        samples are NORMAL and disk is well above the 20 GiB floor.
        """
        result = evaluate(_normal_history())
        assert result.proposed_state == SentinelState.NORMAL

    def test_when_evaluate_called_twice_with_same_history_then_proposals_match(self):
        """evaluate() is a pure proposal: same history → identical proposed_state and reason.

        A side-effecting 'action' would change external state between two identical
        calls; a pure proposal must return consistent results.
        """
        history = _warn_history()
        first = evaluate(history)
        second = evaluate(history)

        assert first.proposed_state == second.proposed_state
        assert first.reason == second.reason
