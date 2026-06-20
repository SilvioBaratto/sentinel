"""
Source-blind unit tests for VerifiedKiller — Issue #21.
Authored from acceptance criteria only. No src/ files were read.

Design contract enforced by these tests:

  VerifiedKiller(
      config: SentinelConfig,
      *,
      quit_sender: Callable[[int], None],
      alive_checker: Callable[[int], bool],
      signal_sender: Callable[[int, int], None],
      sleeper: Callable[[float], None],
  )

  SentinelConfig fields expected by these tests:
    editor_names: list[str]
    editor_auto_sigkill: bool            (default False)
    quit_grace_seconds: float            (e.g. 30.0)
    editor_quit_grace_seconds: float     (longer, e.g. 60.0)
    critical_quit_grace_seconds: float   (shorter, e.g. 5.0)
    sigterm_grace_seconds: float         (e.g. 20.0)

  Candidate stand-in (FakeCandidate):
    pid: int
    name: str
    pressure_level: int   (2 = WARN, 4 = CRITICAL)

  ActionResult fields:
    .kind          → ActionKind.KILL_PROCESS
    .reversibility → Reversibility.PERMANENT
    .success       → bool
    .outcome       → KillOutcome  (EXITED | SURVIVED | SKIPPED | ERROR)
    .stage         → KillStage   (QUIT | SIGTERM | SIGKILL)

  Killer protocol must be @runtime_checkable for the isinstance assertion to pass.
"""

from __future__ import annotations

import signal as _signals
from dataclasses import dataclass, field
from typing import Callable, List, Tuple

from hypothesis import given, settings, strategies as st

from sentinel.execute.verified_killer import VerifiedKiller
from sentinel.domain.protocols import Killer
from sentinel.domain.value_objects import (
    ActionResult,
    ActionKind,
    Reversibility,
    KillOutcome,
    KillStage,
)
from sentinel.config import SentinelConfig


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeCandidate:
    """Stand-in for ProcessCandidate — shaped from the spec, not from src/."""

    pid: int = 9999
    name: str = "Slack"
    pressure_level: int = 2  # 2 = WARN, 4 = CRITICAL
    create_time: float | None = None  # used by PID-reuse guard tests


@dataclass
class SignalSpy:
    """Records every (pid, sig) pair passed to the signal_sender slot."""

    _calls: List[Tuple[int, int]] = field(default_factory=list)

    def __call__(self, pid: int, sig: int) -> None:
        self._calls.append((pid, sig))

    def signal_numbers(self) -> List[int]:
        return [sig for _, sig in self._calls]


class AliveSequence:
    """
    Returns pre-programmed alive booleans in sequence; repeats the last value forever.

    True  → process is still alive.
    False → process has exited (dead).
    """

    def __init__(self, *responses: bool) -> None:
        self._seq = list(responses) if responses else [False]
        self._idx = 0

    def __call__(self, pid: int) -> bool:
        val = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        return val


@dataclass
class SleepSpy:
    """Records durations passed to the sleeper slot."""

    durations: List[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_config(**overrides) -> SentinelConfig:
    defaults = dict(
        editor_names=["Code", "Cursor", "PyCharm", "IDEA"],
        editor_auto_sigkill=False,
        quit_grace_seconds=30.0,
        editor_quit_grace_seconds=60.0,
        critical_quit_grace_seconds=5.0,
        sigterm_grace_seconds=20.0,
    )
    return SentinelConfig(**{**defaults, **overrides})


def make_killer(
    *,
    config: SentinelConfig | None = None,
    alive_checker: Callable[[int], bool] | None = None,
    signal_sender: Callable[[int, int], None] | None = None,
    sleeper: Callable[[float], None] | None = None,
    quit_sender: Callable[[int], None] | None = None,
    create_time_reader: Callable[[int], "float | None"] | None = None,
) -> VerifiedKiller:
    return VerifiedKiller(
        config=config or make_config(),
        quit_sender=quit_sender or (lambda pid: None),
        alive_checker=alive_checker or AliveSequence(False),
        signal_sender=signal_sender or SignalSpy(),
        sleeper=sleeper or SleepSpy(),
        create_time_reader=create_time_reader or (lambda pid: None),
    )


# ---------------------------------------------------------------------------
# Criterion: VerifiedKiller implements Killer — kill(candidate) -> ActionResult
# ---------------------------------------------------------------------------


class TestImplementsKillerProtocol:
    def test_when_kill_is_called_then_an_action_result_is_returned(self):
        """kill(candidate) must return an ActionResult instance."""
        result = make_killer().kill(FakeCandidate())
        assert isinstance(result, ActionResult)

    def test_when_verified_killer_is_instantiated_then_it_satisfies_killer_protocol(
        self,
    ):
        """VerifiedKiller is a structural subtype of Killer (@runtime_checkable required)."""
        assert isinstance(make_killer(), Killer)


# ---------------------------------------------------------------------------
# Criterion: Verify-before-escalate
# ---------------------------------------------------------------------------


class TestVerifyBeforeEscalate:
    def test_when_process_exits_during_quit_grace_then_no_signals_are_sent(self):
        """
        Criterion: SIGTERM/SIGKILL are NEVER sent when the process exits during quit grace.
        Modelled as alive_checker returning False on its first call (dead after quit grace).
        """
        spy = SignalSpy()
        make_killer(alive_checker=AliveSequence(False), signal_sender=spy).kill(
            FakeCandidate()
        )

        assert spy._calls == [], f"Expected zero signal calls, got: {spy._calls}"

    def test_when_process_exits_during_sigterm_grace_then_sigkill_is_not_sent(self):
        """
        Criterion: SIGKILL is never sent if the process exits during SIGTERM grace.
        alive_checker: True (alive after quit) → False (dead after SIGTERM grace).
        """
        spy = SignalSpy()
        make_killer(
            alive_checker=AliveSequence(True, False),
            signal_sender=spy,
        ).kill(FakeCandidate())

        assert _signals.SIGKILL not in spy.signal_numbers(), (
            f"Expected no SIGKILL; signals sent: {spy.signal_numbers()}"
        )
        assert _signals.SIGTERM in spy.signal_numbers(), (
            "Expected SIGTERM to have been sent (process was alive after quit grace)"
        )


# ---------------------------------------------------------------------------
# Criterion: Editor behaviour — longer grace, no auto-SIGKILL by default
# ---------------------------------------------------------------------------


class TestEditorBehaviour:
    def test_when_candidate_is_editor_then_editor_quit_grace_seconds_is_used(self):
        """
        Editors use editor_quit_grace_seconds (60 s) as the quit grace,
        not the standard quit_grace_seconds (30 s).
        """
        sleep_spy = SleepSpy()
        config = make_config(
            editor_names=["Code"],
            editor_quit_grace_seconds=60.0,
            quit_grace_seconds=30.0,
        )
        make_killer(
            config=config,
            alive_checker=AliveSequence(False),
            sleeper=sleep_spy,
        ).kill(FakeCandidate(name="Code"))

        assert sleep_spy.durations, "Expected at least one sleep call for quit grace"
        assert sleep_spy.durations[0] == 60.0, (
            f"Expected editor quit grace 60.0 s, got {sleep_spy.durations[0]} s"
        )

    def test_when_editor_auto_sigkill_is_false_and_process_survives_then_outcome_is_survived_at_sigterm_stage(
        self,
    ):
        """
        editor_auto_sigkill=False (default) → SIGKILL never sent to editor;
        outcome = SURVIVED, stage = SIGTERM.
        """
        spy = SignalSpy()
        config = make_config(editor_names=["Code"], editor_auto_sigkill=False)
        result = make_killer(
            config=config,
            alive_checker=AliveSequence(True, True, True),
            signal_sender=spy,
        ).kill(FakeCandidate(name="Code"))

        assert _signals.SIGKILL not in spy.signal_numbers(), (
            "SIGKILL must not be sent to an editor when editor_auto_sigkill=False"
        )
        assert result.outcome == KillOutcome.SURVIVED
        assert result.stage == KillStage.SIGTERM

    def test_when_editor_auto_sigkill_is_true_and_process_survives_then_sigkill_is_sent(
        self,
    ):
        """
        editor_auto_sigkill=True overrides the safe default; SIGKILL escalation proceeds.
        alive_checker: True after quit, True after SIGTERM → SIGKILL is sent.
        """
        spy = SignalSpy()
        config = make_config(editor_names=["Code"], editor_auto_sigkill=True)
        make_killer(
            config=config,
            alive_checker=AliveSequence(True, True, False),
            signal_sender=spy,
        ).kill(FakeCandidate(name="Code"))

        assert _signals.SIGKILL in spy.signal_numbers(), (
            "SIGKILL must be sent when editor_auto_sigkill=True and process survives SIGTERM"
        )


# ---------------------------------------------------------------------------
# Criterion: CRITICAL state → critical_quit_grace_seconds (faster grace)
# ---------------------------------------------------------------------------


class TestCriticalStateGrace:
    def test_when_pressure_level_is_critical_then_critical_quit_grace_seconds_is_used(
        self,
    ):
        """
        pressure_level=4 (CRITICAL) → first sleep equals critical_quit_grace_seconds (5 s),
        not the standard quit_grace_seconds (30 s).
        """
        sleep_spy = SleepSpy()
        config = make_config(critical_quit_grace_seconds=5.0, quit_grace_seconds=30.0)
        make_killer(
            config=config,
            alive_checker=AliveSequence(False),
            sleeper=sleep_spy,
        ).kill(FakeCandidate(pressure_level=4))

        assert sleep_spy.durations, "Expected a sleep call for the quit grace period"
        assert sleep_spy.durations[0] == 5.0, (
            f"Expected critical quit grace 5.0 s, got {sleep_spy.durations[0]} s"
        )

    def test_when_pressure_level_is_warn_then_standard_quit_grace_seconds_is_used(self):
        """
        pressure_level=2 (WARN) → first sleep equals quit_grace_seconds (30 s).
        """
        sleep_spy = SleepSpy()
        config = make_config(critical_quit_grace_seconds=5.0, quit_grace_seconds=30.0)
        make_killer(
            config=config,
            alive_checker=AliveSequence(False),
            sleeper=sleep_spy,
        ).kill(FakeCandidate(pressure_level=2))

        assert sleep_spy.durations, "Expected a sleep call for the quit grace period"
        assert sleep_spy.durations[0] == 30.0, (
            f"Expected standard quit grace 30.0 s, got {sleep_spy.durations[0]} s"
        )


# ---------------------------------------------------------------------------
# Criterion: ActionResult fields + never raises
# ---------------------------------------------------------------------------


class TestActionResultContract:
    def test_when_process_exits_after_quit_then_kind_is_kill_process(self):
        """ActionResult.kind must always equal ActionKind.KILL_PROCESS."""
        result = make_killer(alive_checker=AliveSequence(False)).kill(FakeCandidate())
        assert result.kind == ActionKind.KILL_PROCESS

    def test_when_process_exits_after_quit_then_reversibility_is_permanent(self):
        """ActionResult.reversibility must always equal Reversibility.PERMANENT."""
        result = make_killer(alive_checker=AliveSequence(False)).kill(FakeCandidate())
        assert result.reversibility == Reversibility.PERMANENT

    def test_when_process_exits_after_quit_grace_then_outcome_is_exited_stage_is_quit_success_is_true(
        self,
    ):
        """Process dead after quit grace → outcome=EXITED, stage=QUIT, success=True."""
        result = make_killer(alive_checker=AliveSequence(False)).kill(FakeCandidate())
        assert result.outcome == KillOutcome.EXITED
        assert result.stage == KillStage.QUIT
        assert result.success is True

    def test_when_process_exits_after_sigterm_grace_then_outcome_is_exited_stage_is_sigterm(
        self,
    ):
        """Process dead after SIGTERM grace → outcome=EXITED, stage=SIGTERM."""
        result = make_killer(alive_checker=AliveSequence(True, False)).kill(
            FakeCandidate()
        )
        assert result.outcome == KillOutcome.EXITED
        assert result.stage == KillStage.SIGTERM

    def test_when_editor_survives_and_auto_sigkill_is_false_then_success_is_false(self):
        """SURVIVED outcome (editor, no SIGKILL) → success must be False."""
        config = make_config(editor_names=["Code"], editor_auto_sigkill=False)
        result = make_killer(
            config=config,
            alive_checker=AliveSequence(True, True, True),
        ).kill(FakeCandidate(name="Code"))
        assert result.outcome == KillOutcome.SURVIVED
        assert result.success is False

    def test_when_action_result_has_outcome_field_then_it_is_a_kill_outcome_member(
        self,
    ):
        """ActionResult.outcome is always a KillOutcome enum member."""
        result = make_killer(alive_checker=AliveSequence(False)).kill(FakeCandidate())
        assert isinstance(result.outcome, KillOutcome)

    def test_when_action_result_has_stage_field_then_it_is_a_kill_stage_member(self):
        """ActionResult.stage is always a KillStage enum member."""
        result = make_killer(alive_checker=AliveSequence(False)).kill(FakeCandidate())
        assert isinstance(result.stage, KillStage)

    def test_when_internal_exception_occurs_then_kill_returns_error_outcome_without_raising(
        self,
    ):
        """
        Criterion: kill() never raises.
        Simulated by injecting an alive_checker that throws; kill() must absorb the
        exception and return an ActionResult with outcome=ERROR.
        """

        def exploding_checker(pid: int) -> bool:
            raise RuntimeError("simulated OS failure")

        result = make_killer(alive_checker=exploding_checker).kill(FakeCandidate())
        assert isinstance(result, ActionResult), (
            "kill() must return ActionResult even when internals raise"
        )
        assert result.outcome == KillOutcome.ERROR


# ---------------------------------------------------------------------------
# Fix (fix/exec-safety): SIGKILL must be VERIFIED, not assumed
# Spec table: "verify exit each step".  The final SIGKILL was previously reported
# as EXITED/success without re-checking liveness.
# ---------------------------------------------------------------------------


class TestSigkillIsVerified:
    def test_when_process_survives_sigkill_then_outcome_is_survived_not_exited(self):
        """Non-editor still alive after SIGKILL → outcome=SURVIVED, success=False."""
        spy = SignalSpy()
        result = make_killer(
            # alive after quit, after SIGTERM, AND after SIGKILL (e.g. D-state)
            alive_checker=AliveSequence(True, True, True),
            signal_sender=spy,
        ).kill(FakeCandidate(name="Slack"))

        assert _signals.SIGKILL in spy.signal_numbers(), "SIGKILL must still be sent"
        assert result.outcome == KillOutcome.SURVIVED
        assert result.stage == KillStage.SIGKILL
        assert result.success is False

    def test_when_process_dies_after_sigkill_then_outcome_is_exited(self):
        """Process gone on the post-SIGKILL re-check → outcome=EXITED, success=True."""
        result = make_killer(
            alive_checker=AliveSequence(True, True, False),
        ).kill(FakeCandidate(name="Slack"))

        assert result.outcome == KillOutcome.EXITED
        assert result.stage == KillStage.SIGKILL
        assert result.success is True


# ---------------------------------------------------------------------------
# PID-reuse guard (issue #21 comment)
# ---------------------------------------------------------------------------


class TestPidReuseGuard:
    def test_when_pid_reused_with_different_create_time_during_sigterm_grace_then_no_sigkill_is_sent(
        self,
    ):
        """
        If a new process takes the same pid with a different create_time after SIGTERM
        grace, the killer treats the original process as EXITED and never sends SIGKILL.
        Prevents escalating a kill to a different (possibly protected) process.
        """
        original_ct = 1000.0
        reused_ct = 2000.0
        spy = SignalSpy()
        call_n = [0]

        def create_time_reader(pid: int) -> float:
            call_n[0] += 1
            # 1st read (after quit grace): same create_time → still our process
            # 2nd read (after SIGTERM grace): different create_time → pid recycled
            return original_ct if call_n[0] == 1 else reused_ct

        killer = VerifiedKiller(
            config=make_config(),
            quit_sender=lambda pid: None,
            alive_checker=AliveSequence(True, True),  # pid "exists" at every check
            signal_sender=spy,
            sleeper=SleepSpy(),
            create_time_reader=create_time_reader,
        )
        result = killer.kill(
            FakeCandidate(
                pid=9999, name="Slack", pressure_level=2, create_time=original_ct
            )
        )

        assert _signals.SIGKILL not in spy.signal_numbers(), (
            "SIGKILL must not be sent when the pid was recycled by a different process"
        )
        assert result.outcome == KillOutcome.EXITED


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# Invariant: kill() NEVER raises for any valid candidate input.
# Derived from the "never raises" criterion — a total function over valid inputs.
# ---------------------------------------------------------------------------


@settings(max_examples=60)
@given(
    pid=st.integers(min_value=1, max_value=99999),
    name=st.text(min_size=1, max_size=64),
    pressure_level=st.sampled_from([2, 4]),
)
def test_when_kill_is_called_with_any_valid_candidate_then_action_result_is_returned_without_raising(
    pid: int,
    name: str,
    pressure_level: int,
) -> None:
    """
    Invariant (never-raises): kill(candidate) returns ActionResult for every valid
    combination of pid, name, and pressure_level — it never raises.
    """
    result = make_killer(alive_checker=AliveSequence(False)).kill(
        FakeCandidate(pid=pid, name=name, pressure_level=pressure_level)
    )
    assert isinstance(result, ActionResult)
