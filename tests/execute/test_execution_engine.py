"""
Source-blind example tests for Issue #28:
  feat: execution engine + auto/confirm/dry-run modes + build_executor composition root

This file covers ExecutionEngine behaviour (AC1–AC6).
Authored from acceptance criteria only — no implementation source was read.

Assumed module layout (the implementation MUST export these names):

    sentinel.execute
        build_executor(config: ExecuteConfig, *, components=None) -> ExecutionEngine
        ExecutionEngine

    ExecutionEngine.execute(detection: DetectionResult, state: SentinelState)
        -> tuple[ActionResult, ...]
    ExecutionEngine.pending() -> sequence   (CONFIRM mode only; queued planned actions)

    components dict keys when injecting fakes:
        "killer"   — Killer protocol:  kill(ProcessCandidate) -> ActionResult
        "stopper"  — ContainerStopper: stop(ContainerCandidate) -> ActionResult
        "cleaner"  — DiskCleaner:      clean(SentinelState) -> tuple[ActionResult, ...]
        "audit"    — AuditLogger:      record(AuditRecord) -> None
        "notifier" — Notifier:         notify(ActionResult) -> None

Design assumptions (simplest behaviour consistent with criteria text):
    AUTO    — killer/stopper/cleaner called; audit.record + notifier.notify per action.
    DRY_RUN — no executor called; synthesised ActionResults carry dry_run=True;
              audit records each "would" action (detail contains "would").
    CONFIRM — no executor called; execute() returns (); pending() returns queued actions;
              audit records each "queued" action (detail contains "queued").
    NORMAL  — empty result; no executor called.
    WARN/CRITICAL — killer + stopper called per candidate; cleaner NOT called.
    DISK_LOW      — killer + stopper called per candidate; cleaner IS called.

Skipped criteria (oracle: NOT VERIFIABLE):
    "All tests pass"       — boilerplate suite gate; no per-criterion assertion.
    "SOLID, clean code"    — subjective code-quality prose; no runtime assertion.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from sentinel.config import ExecuteConfig
from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    AuditRecord,
    ContainerCandidate,
    DetectionResult,
    ExecutionMode,
    KillOutcome,
    KillStage,
    ProcessCandidate,
    ProcessInfo,
    Reversibility,
    SentinelState,
)


# ── lazy imports (contract surface only — no implementation read) ──────────────


def _build_executor():
    from sentinel.execute import build_executor  # noqa: PLC0415

    return build_executor


def _engine_cls():
    from sentinel.execute import ExecutionEngine  # noqa: PLC0415

    return ExecutionEngine


# ── constants ──────────────────────────────────────────────────────────────────

_TWO_HOURS: float = 7200.0

_NON_NORMAL_STATES = [
    SentinelState.WARN,
    SentinelState.CRITICAL,
    SentinelState.DISK_LOW,
]
_KILL_STOP_STATES = [SentinelState.WARN, SentinelState.CRITICAL, SentinelState.DISK_LOW]
_CLEANUP_STATES = [SentinelState.DISK_LOW]
_NO_CLEANUP_STATES = [SentinelState.WARN, SentinelState.CRITICAL]


# ── domain builders ───────────────────────────────────────────────────────────


def _make_process_candidate(pid: int = 1, name: str = "Chrome") -> ProcessCandidate:
    info = ProcessInfo(
        pid=pid,
        ppid=0,
        name=name,
        cmdline=(name,),
        has_tty=False,
        tty=None,
        pgid=None,
        cpu_percent=0.2,
        rss_bytes=0,
        create_time=None,
        idle_seconds=_TWO_HOURS + 1.0,
    )
    return ProcessCandidate(
        info=info,
        idle_seconds=_TWO_HOURS + 1.0,
        cpu_percent=0.2,
        reason=f"{name} idle 2h01m",
    )


def _make_container_candidate(name: str = "clipcraft_api") -> ContainerCandidate:
    return ContainerCandidate(
        name=name,
        container_id="deadbeef",
        idle_seconds=_TWO_HOURS + 60.0,
        cpu_percent=0.1,
        reason=f"{name} idle 2h01m",
    )


def _make_detection(
    processes: tuple = (),
    containers: tuple = (),
) -> DetectionResult:
    return DetectionResult(processes=processes, containers=containers)


def _ok_kill_result(target: str = "Chrome") -> ActionResult:
    return ActionResult(
        kind=ActionKind.KILL_PROCESS,
        target=target,
        success=True,
        reversibility=Reversibility.PERMANENT,
        outcome=KillOutcome.EXITED,
        stage=KillStage.QUIT,
    )


def _ok_stop_result(target: str = "clipcraft_api") -> ActionResult:
    return ActionResult(
        kind=ActionKind.STOP_CONTAINER,
        target=target,
        success=True,
        reversibility=Reversibility.REVERSIBLE,
    )


# ── spy fakes derived from acceptance-criteria text ───────────────────────────


class _SpyKiller:
    """Spy satisfying Killer protocol.  kill() records calls and optionally raises."""

    def __init__(
        self,
        *,
        result: ActionResult | None = None,
        raises: bool = False,
        raises_for: str | None = None,
    ) -> None:
        self.calls: list[ProcessCandidate] = []
        self._result = result
        self._raises = raises
        self._raises_for = raises_for

    def kill(self, candidate: ProcessCandidate) -> ActionResult:
        self.calls.append(candidate)
        if self._raises or (
            self._raises_for is not None and candidate.info.name == self._raises_for
        ):
            raise RuntimeError("simulated kill failure")
        return self._result or _ok_kill_result(candidate.info.name)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyStopper:
    """Spy satisfying ContainerStopper protocol.  stop() records calls and optionally raises."""

    def __init__(
        self,
        *,
        result: ActionResult | None = None,
        raises: bool = False,
        raises_for: str | None = None,
    ) -> None:
        self.calls: list[ContainerCandidate] = []
        self._result = result
        self._raises = raises
        self._raises_for = raises_for

    def stop(self, candidate: ContainerCandidate) -> ActionResult:
        self.calls.append(candidate)
        if self._raises or (
            self._raises_for is not None and candidate.name == self._raises_for
        ):
            raise RuntimeError("simulated stop failure")
        return self._result or _ok_stop_result(candidate.name)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyCleaner:
    """Spy satisfying DiskCleaner protocol.  clean() records calls and optionally raises."""

    def __init__(
        self,
        *,
        results: tuple[ActionResult, ...] = (),
        raises: bool = False,
    ) -> None:
        self.calls: list[SentinelState] = []
        self._results = results
        self._raises = raises

    def clean(self, state: SentinelState) -> tuple[ActionResult, ...]:
        self.calls.append(state)
        if self._raises:
            raise RuntimeError("simulated cleaner failure")
        return self._results

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyAudit:
    """Spy satisfying AuditLogger protocol.  record() captures every AuditRecord."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, rec: AuditRecord) -> None:
        self.records.append(rec)

    @property
    def call_count(self) -> int:
        return len(self.records)


class _SpyNotifier:
    """Spy satisfying Notifier protocol.  notify() captures every ActionResult."""

    def __init__(self) -> None:
        self.notified: list[ActionResult] = []

    def notify(self, result: ActionResult) -> None:
        self.notified.append(result)

    @property
    def call_count(self) -> int:
        return len(self.notified)


# ── engine factory helper ──────────────────────────────────────────────────────


def _make_engine(
    *,
    mode: ExecutionMode = ExecutionMode.AUTO,
    killer: _SpyKiller | None = None,
    stopper: _SpyStopper | None = None,
    cleaner: _SpyCleaner | None = None,
    audit: _SpyAudit | None = None,
    notifier: _SpyNotifier | None = None,
) -> tuple[object, _SpyKiller, _SpyStopper, _SpyCleaner, _SpyAudit, _SpyNotifier]:
    """Build an ExecutionEngine with scripted fakes; returns (engine, killer, stopper, cleaner, audit, notifier)."""
    killer = killer or _SpyKiller()
    stopper = stopper or _SpyStopper()
    cleaner = cleaner or _SpyCleaner()
    audit = audit or _SpyAudit()
    notifier = notifier or _SpyNotifier()
    config = ExecuteConfig(mode=mode)
    engine = _build_executor()(
        config,
        components={
            "killer": killer,
            "stopper": stopper,
            "cleaner": cleaner,
            "audit": audit,
            "notifier": notifier,
        },
    )
    return engine, killer, stopper, cleaner, audit, notifier


# ══════════════════════════════════════════════════════════════════════════════
# AC1 — ExecutionEngine.execute() routes candidates to the right executors
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteRouting:
    """AC1: process candidates → killer; container candidates → stopper; cleaner.clean(state) for disk."""

    def test_when_process_candidate_present_and_warn_then_killer_is_called(self):
        proc = _make_process_candidate(pid=1, name="Chrome")
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine()
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 1

    def test_when_process_candidate_present_and_killer_called_then_candidate_is_the_argument(
        self,
    ):
        proc = _make_process_candidate(pid=42, name="Slack")
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine()
        engine.execute(detection, SentinelState.WARN)
        assert any(c.info.pid == 42 for c in killer.calls)

    def test_when_container_candidate_present_and_warn_then_stopper_is_called(self):
        cont = _make_container_candidate("clipcraft_api")
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine()
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == 1

    def test_when_container_candidate_present_and_stopper_called_then_candidate_is_the_argument(
        self,
    ):
        cont = _make_container_candidate("clipcraft_frontend")
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine()
        engine.execute(detection, SentinelState.WARN)
        assert any(c.name == "clipcraft_frontend" for c in stopper.calls)

    def test_when_disk_low_then_cleaner_clean_is_called(self):
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine()
        engine.execute(detection, SentinelState.DISK_LOW)
        assert cleaner.call_count == 1

    def test_when_disk_low_then_cleaner_receives_disk_low_state(self):
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine()
        engine.execute(detection, SentinelState.DISK_LOW)
        assert SentinelState.DISK_LOW in cleaner.calls

    def test_when_both_candidates_present_and_warn_then_both_killer_and_stopper_called(
        self,
    ):
        proc = _make_process_candidate(pid=1, name="Chrome")
        cont = _make_container_candidate("clipcraft_api")
        detection = _make_detection(processes=(proc,), containers=(cont,))
        engine, killer, stopper, *_ = _make_engine()
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 1
        assert stopper.call_count == 1

    def test_when_execute_called_then_a_tuple_is_returned(self):
        detection = _make_detection()
        engine, *_ = _make_engine()
        result = engine.execute(detection, SentinelState.WARN)
        assert isinstance(result, tuple)


# ══════════════════════════════════════════════════════════════════════════════
# AC2 — State gating: NORMAL → empty; cleanup only on DISK_LOW; kills/stops
#        only in WARN/CRITICAL/DISK_LOW
# ══════════════════════════════════════════════════════════════════════════════


class TestStateGating:
    """AC2: NORMAL → no actions; DISK_LOW gates cleanup; WARN/CRITICAL/DISK_LOW gate kills/stops."""

    def test_when_state_is_normal_then_result_is_empty(self):
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        detection = _make_detection(processes=(proc,), containers=(cont,))
        engine, *_ = _make_engine()
        result = engine.execute(detection, SentinelState.NORMAL)
        assert result == ()

    def test_when_state_is_normal_then_killer_is_not_called(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine()
        engine.execute(detection, SentinelState.NORMAL)
        assert killer.call_count == 0

    def test_when_state_is_normal_then_stopper_is_not_called(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine()
        engine.execute(detection, SentinelState.NORMAL)
        assert stopper.call_count == 0

    def test_when_state_is_normal_then_cleaner_is_not_called(self):
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine()
        engine.execute(detection, SentinelState.NORMAL)
        assert cleaner.call_count == 0

    @pytest.mark.parametrize("state", _NO_CLEANUP_STATES)
    def test_when_state_is_not_disk_low_then_cleaner_is_not_called(
        self, state: SentinelState
    ):
        """Cleanup fires only on DISK_LOW (AC2)."""
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine()
        engine.execute(detection, state)
        assert cleaner.call_count == 0, (
            f"cleaner called in {state} — must only fire on DISK_LOW"
        )

    @pytest.mark.parametrize("state", _KILL_STOP_STATES)
    def test_when_state_is_active_then_killer_is_called_for_each_process_candidate(
        self, state: SentinelState
    ):
        """kills/stops fire in WARN/CRITICAL/DISK_LOW (AC2)."""
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine()
        engine.execute(detection, state)
        assert killer.call_count == 1, f"killer not called in {state}"

    @pytest.mark.parametrize("state", _KILL_STOP_STATES)
    def test_when_state_is_active_then_stopper_is_called_for_each_container_candidate(
        self, state: SentinelState
    ):
        """kills/stops fire in WARN/CRITICAL/DISK_LOW (AC2)."""
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine()
        engine.execute(detection, state)
        assert stopper.call_count == 1, f"stopper not called in {state}"


# ══════════════════════════════════════════════════════════════════════════════
# AC3 — DRY_RUN: executors never called; results carry dry_run=True; audit records
# ══════════════════════════════════════════════════════════════════════════════


class TestDryRunMode:
    """AC3: DRY_RUN — executors untouched; returned ActionResults have dry_run=True; audit records "would" actions."""

    def test_when_dry_run_then_killer_call_count_is_zero(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine(mode=ExecutionMode.DRY_RUN)
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 0

    def test_when_dry_run_then_stopper_call_count_is_zero(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine(mode=ExecutionMode.DRY_RUN)
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == 0

    def test_when_dry_run_and_disk_low_then_cleaner_call_count_is_zero(self):
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine(mode=ExecutionMode.DRY_RUN)
        engine.execute(detection, SentinelState.DISK_LOW)
        assert cleaner.call_count == 0

    def test_when_dry_run_then_returned_results_carry_dry_run_true(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, *_ = _make_engine(mode=ExecutionMode.DRY_RUN)
        results = engine.execute(detection, SentinelState.WARN)
        assert len(results) >= 1, (
            "DRY_RUN must return synthesised ActionResults for each candidate"
        )
        assert all(r.dry_run for r in results), (
            "all results must carry dry_run=True in DRY_RUN mode"
        )

    def test_when_dry_run_then_audit_records_each_would_action(self):
        """DRY_RUN audit invariant: every planned action is recorded (detail contains 'would')."""
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.DRY_RUN)
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1, (
            "audit.record must be called for DRY_RUN 'would' actions"
        )
        assert any("would" in r.detail.lower() for r in audit.records), (
            "at least one audit record must note 'would' in DRY_RUN mode"
        )

    def test_when_dry_run_then_audit_records_dry_run_mode(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.DRY_RUN)
        engine.execute(detection, SentinelState.WARN)
        assert all(r.mode == ExecutionMode.DRY_RUN for r in audit.records)


# ══════════════════════════════════════════════════════════════════════════════
# AC4 — CONFIRM: nothing executed; pending() exposes queued actions; audit notes "queued"
# ══════════════════════════════════════════════════════════════════════════════


class TestConfirmMode:
    """AC4: CONFIRM — executors untouched; pending() returns queued actions; audit notes 'queued'."""

    def test_when_confirm_then_killer_call_count_is_zero(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 0

    def test_when_confirm_then_stopper_call_count_is_zero(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == 0

    def test_when_confirm_and_disk_low_then_cleaner_call_count_is_zero(self):
        detection = _make_detection()
        engine, _, _, cleaner, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.DISK_LOW)
        assert cleaner.call_count == 0

    def test_when_confirm_and_process_candidate_then_pending_is_non_empty(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        queued = engine.pending()
        assert len(list(queued)) >= 1, (
            "pending() must expose the queued process kill action"
        )

    def test_when_confirm_and_container_candidate_then_pending_is_non_empty(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        queued = engine.pending()
        assert len(list(queued)) >= 1, (
            "pending() must expose the queued container stop action"
        )

    def test_when_confirm_and_no_candidates_then_pending_is_empty(self):
        detection = _make_detection()
        engine, *_ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        queued = engine.pending()
        assert len(list(queued)) == 0

    def test_when_confirm_then_audit_records_queued_actions(self):
        """CONFIRM audit invariant: queued actions are recorded (detail contains 'queued')."""
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1, (
            "audit.record must be called for CONFIRM queued actions"
        )
        assert any("queued" in r.detail.lower() for r in audit.records), (
            "at least one audit record must note 'queued' in CONFIRM mode"
        )

    def test_when_confirm_then_audit_records_confirm_mode(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.CONFIRM)
        engine.execute(detection, SentinelState.WARN)
        assert all(r.mode == ExecutionMode.CONFIRM for r in audit.records)


# ══════════════════════════════════════════════════════════════════════════════
# AC5 — AUTO: each executor called once per candidate; audit + notifier once per action
# ══════════════════════════════════════════════════════════════════════════════


class TestAutoMode:
    """AC5: AUTO — each executor once per candidate; audit.record + notifier.notify once per resulting action."""

    def test_when_auto_and_two_process_candidates_then_killer_called_exactly_twice(
        self,
    ):
        procs = (
            _make_process_candidate(pid=1, name="Chrome"),
            _make_process_candidate(pid=2, name="Slack"),
        )
        detection = _make_detection(processes=procs)
        engine, killer, *_ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 2

    def test_when_auto_and_two_container_candidates_then_stopper_called_exactly_twice(
        self,
    ):
        conts = (
            _make_container_candidate("clipcraft_api"),
            _make_container_candidate("clipcraft_frontend"),
        )
        detection = _make_detection(containers=conts)
        engine, _, stopper, *_ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == 2

    def test_when_auto_and_one_process_then_audit_record_called_at_least_once(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1

    def test_when_auto_and_one_process_then_notifier_notify_called_at_least_once(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, _, notifier = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert notifier.call_count >= 1

    def test_when_auto_and_two_processes_then_audit_records_match_action_count(self):
        """Each resulting action produces exactly one audit record (AC5)."""
        procs = (
            _make_process_candidate(pid=1, name="Chrome"),
            _make_process_candidate(pid=2, name="Slack"),
        )
        detection = _make_detection(processes=procs)
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.AUTO)
        results = engine.execute(detection, SentinelState.WARN)
        assert audit.call_count == len(results)

    def test_when_auto_and_two_processes_then_notifier_calls_match_action_count(self):
        """Each resulting action produces exactly one notifier call (AC5)."""
        procs = (
            _make_process_candidate(pid=1, name="Chrome"),
            _make_process_candidate(pid=2, name="Slack"),
        )
        detection = _make_detection(processes=procs)
        engine, _, _, _, _, notifier = _make_engine(mode=ExecutionMode.AUTO)
        results = engine.execute(detection, SentinelState.WARN)
        assert notifier.call_count == len(results)

    def test_when_auto_and_one_container_then_audit_record_called_once(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, _, _, audit, _ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1

    def test_when_auto_and_one_container_then_notifier_notify_called_once(self):
        cont = _make_container_candidate()
        detection = _make_detection(containers=(cont,))
        engine, _, _, _, _, notifier = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert notifier.call_count >= 1

    def test_when_auto_and_disk_low_cleaner_returns_result_then_that_result_in_output(
        self,
    ):
        """Cleaner results are included in the overall result tuple (AC1 + AC5)."""
        clean_result = ActionResult(
            kind=ActionKind.TRASH,
            target="/Library/Caches/SomeApp",
            success=True,
            reversibility=Reversibility.REVERSIBLE,
        )
        cleaner = _SpyCleaner(results=(clean_result,))
        engine, *_ = _make_engine(mode=ExecutionMode.AUTO, cleaner=cleaner)
        results = engine.execute(_make_detection(), SentinelState.DISK_LOW)
        assert clean_result in results


# ══════════════════════════════════════════════════════════════════════════════
# AC6 — Error isolation: one executor raising does not abort the batch;
#        engine never raises; failure is audited
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorIsolation:
    """AC6: one executor raising must not abort the batch; engine.execute() never propagates exceptions."""

    def test_when_killer_raises_for_one_candidate_then_other_candidate_is_still_processed(
        self,
    ):
        procs = (
            _make_process_candidate(pid=1, name="Chrome"),
            _make_process_candidate(pid=2, name="Slack"),
        )
        killer = _SpyKiller(raises_for="Chrome")
        detection = _make_detection(processes=procs)
        engine, spy_killer, *_ = _make_engine(killer=killer)
        engine.execute(detection, SentinelState.WARN)
        assert spy_killer.call_count == 2, (
            "killer must be called for all candidates even if one raises"
        )

    def test_when_stopper_raises_for_one_candidate_then_other_candidate_is_still_processed(
        self,
    ):
        conts = (
            _make_container_candidate("clipcraft_api"),
            _make_container_candidate("clipcraft_frontend"),
        )
        stopper = _SpyStopper(raises_for="clipcraft_api")
        detection = _make_detection(containers=conts)
        engine, _, spy_stopper, *_ = _make_engine(stopper=stopper)
        engine.execute(detection, SentinelState.WARN)
        assert spy_stopper.call_count == 2, (
            "stopper must be called for all candidates even if one raises"
        )

    def test_when_killer_raises_then_engine_does_not_propagate_the_exception(self):
        proc = _make_process_candidate()
        killer = _SpyKiller(raises=True)
        detection = _make_detection(processes=(proc,))
        engine, *_ = _make_engine(killer=killer)
        result = engine.execute(detection, SentinelState.WARN)
        assert isinstance(result, tuple)

    def test_when_stopper_raises_then_engine_does_not_propagate_the_exception(self):
        cont = _make_container_candidate()
        stopper = _SpyStopper(raises=True)
        detection = _make_detection(containers=(cont,))
        engine, _, *_ = _make_engine(stopper=stopper)
        result = engine.execute(detection, SentinelState.WARN)
        assert isinstance(result, tuple)

    def test_when_cleaner_raises_then_engine_does_not_propagate_the_exception(self):
        cleaner = _SpyCleaner(raises=True)
        engine, *_ = _make_engine(cleaner=cleaner)
        result = engine.execute(_make_detection(), SentinelState.DISK_LOW)
        assert isinstance(result, tuple)

    def test_when_killer_raises_for_one_then_failure_is_audited(self):
        """AC6: failure audited — the failed dispatch produces an audit record."""
        proc = _make_process_candidate()
        killer = _SpyKiller(raises=True)
        audit = _SpyAudit()
        detection = _make_detection(processes=(proc,))
        engine, *_ = _make_engine(killer=killer, audit=audit)
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1, "a failed dispatch must still be audited"

    def test_when_killer_raises_for_one_candidate_then_remaining_results_are_still_returned(
        self,
    ):
        procs = (
            _make_process_candidate(pid=1, name="Chrome"),
            _make_process_candidate(pid=2, name="Slack"),
        )
        killer = _SpyKiller(raises_for="Chrome")
        detection = _make_detection(processes=procs)
        engine, *_ = _make_engine(killer=killer)
        results = engine.execute(detection, SentinelState.WARN)
        slack_results = [r for r in results if "Slack" in r.target]
        assert slack_results, (
            "non-failing dispatch results must be present even when another dispatch raised"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Property-based tests — invariants derived from the acceptance criteria
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteNeverRaisesInvariant:
    """AC6 — Never-raises: execute() is a total function over all SentinelState values."""

    @hyp_settings(max_examples=50)
    @given(state=st.sampled_from(list(SentinelState)))
    def test_when_execute_called_with_any_state_then_no_error_is_raised(
        self, state: SentinelState
    ) -> None:
        """
        Never-raises invariant: execute() must not propagate exceptions for any valid
        SentinelState, regardless of candidate count.

        Derived from AC6: "engine never raises".
        """
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        detection = _make_detection(processes=(proc,), containers=(cont,))
        engine, *_ = _make_engine()
        result = engine.execute(detection, state)
        assert isinstance(result, tuple)


class TestNormalStateAlwaysEmptyInvariant:
    """AC2 — NORMAL-always-empty: any detection → execute(detection, NORMAL) returns ()."""

    @hyp_settings(max_examples=50)
    @given(
        proc_count=st.integers(min_value=0, max_value=5),
        cont_count=st.integers(min_value=0, max_value=5),
    )
    def test_when_state_is_normal_then_result_is_always_empty_regardless_of_candidates(
        self, proc_count: int, cont_count: int
    ) -> None:
        """
        Idempotence invariant: execute(any_detection, NORMAL) == () for all detection inputs.

        Derived from AC2: "state == NORMAL → no actions, empty result".
        """
        procs = tuple(
            _make_process_candidate(pid=i, name=f"App{i}")
            for i in range(1, proc_count + 1)
        )
        conts = tuple(_make_container_candidate(f"svc_{i}") for i in range(cont_count))
        detection = _make_detection(processes=procs, containers=conts)
        engine, *_ = _make_engine()
        result = engine.execute(detection, SentinelState.NORMAL)
        assert result == ()


class TestAutoRoutingCountInvariant:
    """AC5 — Count invariant: N candidates → executor called exactly N times (AUTO mode)."""

    @hyp_settings(max_examples=50)
    @given(n=st.integers(min_value=0, max_value=8))
    def test_when_auto_and_n_process_candidates_then_killer_called_exactly_n_times(
        self, n: int
    ) -> None:
        """
        Ordering/count invariant: killer.call_count == len(detection.processes) in AUTO mode.

        Derived from AC5: "each executor called once per candidate".
        """
        procs = tuple(
            _make_process_candidate(pid=i, name=f"App{i}") for i in range(1, n + 1)
        )
        detection = _make_detection(processes=procs)
        engine, killer, *_ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == n

    @hyp_settings(max_examples=50)
    @given(n=st.integers(min_value=0, max_value=8))
    def test_when_auto_and_n_container_candidates_then_stopper_called_exactly_n_times(
        self, n: int
    ) -> None:
        """
        Ordering/count invariant: stopper.call_count == len(detection.containers) in AUTO mode.

        Derived from AC5: "each executor called once per candidate".
        """
        conts = tuple(_make_container_candidate(f"svc_{i}") for i in range(n))
        detection = _make_detection(containers=conts)
        engine, _, stopper, *_ = _make_engine(mode=ExecutionMode.AUTO)
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == n


class TestDryRunFlagInvariant:
    """AC3 — DRY_RUN flag: all returned ActionResults carry dry_run=True for any non-NORMAL state with candidates."""

    @hyp_settings(max_examples=50)
    @given(
        n=st.integers(min_value=1, max_value=5),
        state=st.sampled_from(_NON_NORMAL_STATES),
    )
    def test_when_dry_run_and_candidates_then_all_results_carry_dry_run_true(
        self, n: int, state: SentinelState
    ) -> None:
        """
        Invariant: in DRY_RUN mode, every returned ActionResult has dry_run=True.

        Derived from AC3: "results carry dry_run=True".
        """
        procs = tuple(
            _make_process_candidate(pid=i, name=f"App{i}") for i in range(1, n + 1)
        )
        detection = _make_detection(processes=procs)
        engine, *_ = _make_engine(mode=ExecutionMode.DRY_RUN)
        results = engine.execute(detection, state)
        if results:
            assert all(r.dry_run for r in results), (
                "every ActionResult in DRY_RUN mode must carry dry_run=True"
            )
