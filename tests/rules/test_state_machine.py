"""
Source-blind tests for SentinelStateMachine (Issue #6).

Authored from acceptance criteria alone — Red phase of TDD.
The implementation does not yet exist; these tests define the contract.

Import assumptions (document any deviation when implementing):
- SentinelStateMachine lives in sentinel.rules.state_machine
- SentinelState and CandidateSignal live in sentinel.domain.value_objects
- transition(signal: CandidateSignal) -> SentinelState reads signal.proposed_state
  and returns the matching SentinelState, updating internal state accordingly

Criteria skipped per oracle (NOT VERIFIABLE at unit level):
- No side effects in NORMAL (spy / no-executor contract — requires executor seam)
- Idempotent re-application (oracle: no concrete runtime check inferable)
- All tests pass (boilerplate suite gate, not a per-criterion assertion)
- SOLID / clean code (subjective prose, no concrete runtime check)
"""

from __future__ import annotations

import inspect

import pytest
from hypothesis import given, strategies as st

from sentinel.domain.value_objects import CandidateSignal, SentinelState
from sentinel.rules.state_machine import SentinelStateMachine
from tests.conftest import make_signal


# ---------------------------------------------------------------------------
# Module-level helper — builds a CandidateSignal for the given proposed state
# ---------------------------------------------------------------------------


def _signal(state: SentinelState) -> CandidateSignal:
    return make_signal(proposed_state=state)


# ---------------------------------------------------------------------------
# Criterion: machine holds `current` starting at NORMAL;
#            `state` property is a read-only poll (reading it never drives
#            a transition)
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_when_machine_is_created_then_state_is_normal(self):
        machine = SentinelStateMachine()

        assert machine.state == SentinelState.NORMAL

    def test_when_state_property_is_read_multiple_times_then_state_remains_normal(self):
        machine = SentinelStateMachine()

        for _ in range(5):
            assert machine.state == SentinelState.NORMAL

    def test_when_state_property_is_assigned_then_attribute_error_is_raised(self):
        """state must be read-only — no setter on the property."""
        machine = SentinelStateMachine()

        with pytest.raises(AttributeError):
            machine.state = SentinelState.WARN  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Criterion: transition(signal) -> SentinelState maps each candidate to its
#            exact state; output is always one of the four enum members
# ---------------------------------------------------------------------------


class TestTransitionMapping:
    @pytest.mark.parametrize(
        "proposed,expected",
        [
            (SentinelState.NORMAL, SentinelState.NORMAL),
            (SentinelState.WARN, SentinelState.WARN),
            (SentinelState.CRITICAL, SentinelState.CRITICAL),
            (SentinelState.DISK_LOW, SentinelState.DISK_LOW),
        ],
    )
    def test_when_signal_is_applied_then_exact_state_is_returned(
        self, proposed: SentinelState, expected: SentinelState
    ) -> None:
        machine = SentinelStateMachine()

        result = machine.transition(_signal(proposed))

        assert result == expected

    @pytest.mark.parametrize("proposed", list(SentinelState))
    def test_when_any_signal_is_applied_then_result_is_a_sentinel_state_member(
        self, proposed: SentinelState
    ) -> None:
        machine = SentinelStateMachine()

        result = machine.transition(_signal(proposed))

        assert isinstance(result, SentinelState)

    def test_when_signal_is_applied_then_machine_state_matches_returned_value(
        self,
    ) -> None:
        """transition must update internal state to mirror the returned SentinelState."""
        machine = SentinelStateMachine()

        for proposed in SentinelState:
            returned = machine.transition(_signal(proposed))
            assert machine.state == returned


# ---------------------------------------------------------------------------
# Criterion: NORMAL → WARN → CRITICAL → DISK_LOW → NORMAL sequence
#            lands on the right state each step
# ---------------------------------------------------------------------------


class TestStateSequence:
    def test_when_full_sequence_is_applied_then_each_step_lands_on_correct_state(
        self,
    ) -> None:
        machine = SentinelStateMachine()
        assert machine.state == SentinelState.NORMAL, "machine must start at NORMAL"

        result = machine.transition(_signal(SentinelState.WARN))
        assert result == SentinelState.WARN
        assert machine.state == SentinelState.WARN

        result = machine.transition(_signal(SentinelState.CRITICAL))
        assert result == SentinelState.CRITICAL
        assert machine.state == SentinelState.CRITICAL

        result = machine.transition(_signal(SentinelState.DISK_LOW))
        assert result == SentinelState.DISK_LOW
        assert machine.state == SentinelState.DISK_LOW

        result = machine.transition(_signal(SentinelState.NORMAL))
        assert result == SentinelState.NORMAL
        assert machine.state == SentinelState.NORMAL


# ---------------------------------------------------------------------------
# Criterion: transition body < 10 lines
# (oracle: [UNIT] — verifiable via inspect.getsource at test time)
# ---------------------------------------------------------------------------


class TestTransitionSize:
    def test_when_transition_body_is_measured_then_it_has_fewer_than_10_substantive_lines(
        self,
    ) -> None:
        source = inspect.getsource(SentinelStateMachine.transition)
        lines = source.splitlines()
        body_lines = [
            line
            for line in lines[1:]  # skip the `def` signature line
            if line.strip() and not line.strip().startswith("#")
        ]
        assert len(body_lines) < 10, (
            f"transition body has {len(body_lines)} substantive lines; "
            "acceptance criterion requires fewer than 10"
        )


# ---------------------------------------------------------------------------
# Criterion: Idempotent — re-applying the same candidate keeps the same state
# ---------------------------------------------------------------------------


class TestIdempotent:
    @pytest.mark.parametrize("state", list(SentinelState))
    def test_when_same_signal_is_applied_twice_then_state_is_unchanged(
        self, state: SentinelState
    ) -> None:
        machine = SentinelStateMachine()
        machine.transition(_signal(state))
        first = machine.state

        machine.transition(_signal(state))

        assert machine.state == first == state


# ---------------------------------------------------------------------------
# Criterion: No side effects in NORMAL ("touch nothing" contract)
#
# Cycle 1 has no executor seam, so the spy-based assertion is deferred to
# Cycle 3 (when an executor protocol is injected). Here we assert the
# observable half: a NORMAL signal produces a NORMAL state with no exception,
# covering the "touch nothing" invariant at the state level.
# ---------------------------------------------------------------------------


class TestNoSideEffectsInNormal:
    def test_when_machine_is_in_normal_then_transition_to_normal_is_a_no_op(
        self,
    ) -> None:
        machine = SentinelStateMachine()
        state_before = machine.state

        machine.transition(_signal(SentinelState.NORMAL))

        assert machine.state == SentinelState.NORMAL == state_before

    def test_when_machine_returns_to_normal_after_warn_then_state_is_normal(
        self,
    ) -> None:
        machine = SentinelStateMachine()
        machine.transition(_signal(SentinelState.WARN))

        machine.transition(_signal(SentinelState.NORMAL))

        assert machine.state == SentinelState.NORMAL


# ---------------------------------------------------------------------------
# Property-based test
#
# Invariant: for ANY valid CandidateSignal (any proposed_state), transition
# returns a value that is always a SentinelState member — no invalid output
# is possible within the stated domain.
#
# Criterion source: "output always one of the four enum members"
# Kind: never-raises-for-valid-input + bounded-output
# ---------------------------------------------------------------------------


@given(st.sampled_from(list(SentinelState)))
def test_when_any_sentinel_state_is_proposed_then_transition_always_returns_a_sentinel_state_member(
    proposed: SentinelState,
) -> None:
    machine = SentinelStateMachine()

    result = machine.transition(_signal(proposed))

    assert isinstance(result, SentinelState)
