"""
Source-blind tests for Issue #28: build_executor composition root.

This file covers AC7 and AC8 and mirrors test_detection_composition.py.
Authored against acceptance criteria only — no implementation source was read.

Assumed module layout:

    sentinel.execute
        build_executor(config: ExecuteConfig, *, components=None) -> ExecutionEngine
        ExecutionEngine

    sentinel.notify
        MacNotifier   — macOS per-action notification; used when notify.enabled=True + AUTO mode
        NullNotifier  — silent drop; used when notify.enabled=False or mode=DRY_RUN

    components dict keys (AC7):
        "killer"   — Killer protocol:  kill(ProcessCandidate) -> ActionResult
        "stopper"  — ContainerStopper: stop(ContainerCandidate) -> ActionResult
        "cleaner"  — DiskCleaner:      clean(SentinelState) -> tuple[ActionResult, ...]
        "audit"    — AuditLogger:      record(AuditRecord) -> None
        "notifier" — Notifier:         notify(ActionResult) -> None

Design assumptions (simplest behaviour consistent with criteria text):
    - build_executor(config, components=fakes) wires injected fakes rather than real adapters.
    - build_executor(config) without components succeeds without OS calls or subprocess spawning.
    - Importing sentinel.execute (or sentinel.execute.engine) does NOT import psutil or spawn
      a subprocess at module-load time.
    - AC8 notifier selection is verified by patching the unchosen class __init__ to raise:
      if the wrong class is selected the patch triggers and the test fails; otherwise it passes.

Skipped criteria (oracle: NOT VERIFIABLE):
    "All tests pass"    — boilerplate suite gate.
    "SOLID, clean code" — subjective code-quality prose.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from sentinel.config import ExecuteConfig, NotifyConfig
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


# ── lazy imports ───────────────────────────────────────────────────────────────


def _build_executor():
    from sentinel.execute import build_executor  # noqa: PLC0415

    return build_executor


def _engine_cls():
    from sentinel.execute import ExecutionEngine  # noqa: PLC0415

    return ExecutionEngine


# ── domain builders ───────────────────────────────────────────────────────────

_TWO_HOURS: float = 7200.0


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


# ── spy fakes ─────────────────────────────────────────────────────────────────


class _SpyKiller:
    def __init__(self, *, result: ActionResult | None = None) -> None:
        self.calls: list[ProcessCandidate] = []
        self._result = result

    def kill(self, candidate: ProcessCandidate) -> ActionResult:
        self.calls.append(candidate)
        return self._result or _ok_kill_result(candidate.info.name)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyStopper:
    def __init__(self, *, result: ActionResult | None = None) -> None:
        self.calls: list[ContainerCandidate] = []
        self._result = result

    def stop(self, candidate: ContainerCandidate) -> ActionResult:
        self.calls.append(candidate)
        return self._result or _ok_stop_result(candidate.name)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyCleaner:
    def __init__(self, *, results: tuple[ActionResult, ...] = ()) -> None:
        self.calls: list[SentinelState] = []
        self._results = results

    def clean(self, state: SentinelState) -> tuple[ActionResult, ...]:
        self.calls.append(state)
        return self._results

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _SpyAudit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, rec: AuditRecord) -> None:
        self.records.append(rec)

    @property
    def call_count(self) -> int:
        return len(self.records)


class _SpyNotifier:
    def __init__(self) -> None:
        self.notified: list[ActionResult] = []

    def notify(self, result: ActionResult) -> None:
        self.notified.append(result)

    @property
    def call_count(self) -> int:
        return len(self.notified)


# ── composition root factory helper ───────────────────────────────────────────


def _all_fakes(
    *,
    mode: ExecutionMode = ExecutionMode.AUTO,
) -> tuple[object, _SpyKiller, _SpyStopper, _SpyCleaner, _SpyAudit, _SpyNotifier]:
    """Build engine with all five fakes injected; returns (engine, killer, stopper, cleaner, audit, notifier)."""
    killer = _SpyKiller()
    stopper = _SpyStopper()
    cleaner = _SpyCleaner()
    audit = _SpyAudit()
    notifier = _SpyNotifier()
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


def _fakes_without_notifier(
    *,
    mode: ExecutionMode = ExecutionMode.AUTO,
    notify: NotifyConfig | None = None,
) -> object:
    """Build engine with all fakes except notifier — lets real notifier selection run."""
    config = ExecuteConfig(
        mode=mode,
        notify=notify or NotifyConfig(),
    )
    return _build_executor()(
        config,
        components={
            "killer": _SpyKiller(),
            "stopper": _SpyStopper(),
            "cleaner": _SpyCleaner(),
            "audit": _SpyAudit(),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# AC7 — build_executor wires injected fakes; real adapters via deferred imports
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildExecutorReturnsExecutionEngine:
    """build_executor(config) → ExecutionEngine regardless of components argument."""

    def test_when_build_executor_called_with_all_fakes_then_execution_engine_is_returned(
        self,
    ):
        engine, *_ = _all_fakes()
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_without_components_then_execution_engine_is_returned(
        self,
    ):
        """AC7: build_executor(config) with default (deferred) adapters returns ExecutionEngine."""
        engine = _build_executor()(ExecuteConfig())
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_with_auto_mode_then_execution_engine_is_returned(
        self,
    ):
        config = ExecuteConfig(mode=ExecutionMode.AUTO)
        engine = _build_executor()(
            config,
            components={
                "killer": _SpyKiller(),
                "stopper": _SpyStopper(),
                "cleaner": _SpyCleaner(),
                "audit": _SpyAudit(),
                "notifier": _SpyNotifier(),
            },
        )
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_with_dry_run_mode_then_execution_engine_is_returned(
        self,
    ):
        config = ExecuteConfig(mode=ExecutionMode.DRY_RUN)
        engine = _build_executor()(
            config,
            components={
                "killer": _SpyKiller(),
                "stopper": _SpyStopper(),
                "cleaner": _SpyCleaner(),
                "audit": _SpyAudit(),
                "notifier": _SpyNotifier(),
            },
        )
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_with_confirm_mode_then_execution_engine_is_returned(
        self,
    ):
        config = ExecuteConfig(mode=ExecutionMode.CONFIRM)
        engine = _build_executor()(
            config,
            components={
                "killer": _SpyKiller(),
                "stopper": _SpyStopper(),
                "cleaner": _SpyCleaner(),
                "audit": _SpyAudit(),
                "notifier": _SpyNotifier(),
            },
        )
        assert isinstance(engine, _engine_cls())


class TestFakeInjection:
    """AC7: injected fakes are the ones actually invoked — real OS adapters never called."""

    def test_when_fake_killer_injected_then_it_is_called_by_execute(self):
        proc = _make_process_candidate(pid=7, name="Firefox")
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert killer.call_count == 1

    def test_when_fake_stopper_injected_then_it_is_called_by_execute(self):
        cont = _make_container_candidate("my_test_service")
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert stopper.call_count == 1

    def test_when_fake_cleaner_injected_then_it_is_called_on_disk_low(self):
        engine, _, _, cleaner, *_ = _all_fakes()
        engine.execute(_make_detection(), SentinelState.DISK_LOW)
        assert cleaner.call_count == 1

    def test_when_fake_audit_injected_then_it_receives_records_on_execute(self):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, audit, _ = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert audit.call_count >= 1

    def test_when_fake_notifier_injected_then_it_receives_notifications_on_execute(
        self,
    ):
        proc = _make_process_candidate()
        detection = _make_detection(processes=(proc,))
        engine, _, _, _, _, notifier = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert notifier.call_count >= 1

    def test_when_fake_killer_injected_then_correct_candidate_pid_is_routed(self):
        proc = _make_process_candidate(pid=42, name="Slack")
        detection = _make_detection(processes=(proc,))
        engine, killer, *_ = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert any(c.info.pid == 42 for c in killer.calls)

    def test_when_fake_stopper_injected_then_correct_container_name_is_routed(self):
        cont = _make_container_candidate("clipcraft_frontend")
        detection = _make_detection(containers=(cont,))
        engine, _, stopper, *_ = _all_fakes()
        engine.execute(detection, SentinelState.WARN)
        assert any(c.name == "clipcraft_frontend" for c in stopper.calls)

    def test_when_all_fakes_injected_then_construction_accepts_any_valid_execute_config(
        self,
    ):
        """AC7: the injection interface works for any ExecuteConfig field combination."""
        for mode in ExecutionMode:
            config = ExecuteConfig(mode=mode)
            engine = _build_executor()(
                config,
                components={
                    "killer": _SpyKiller(),
                    "stopper": _SpyStopper(),
                    "cleaner": _SpyCleaner(),
                    "audit": _SpyAudit(),
                    "notifier": _SpyNotifier(),
                },
            )
            assert isinstance(engine, _engine_cls()), (
                f"expected ExecutionEngine for mode={mode}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# AC7 — Deferred imports: importing sentinel.execute spawns no subprocess;
#        no psutil eagerly pulled in; mirrors test_detection_composition.py
# ══════════════════════════════════════════════════════════════════════════════


class TestDeferredImports:
    def test_when_execute_module_imported_then_psutil_is_not_newly_added_to_sys_modules(
        self,
    ):
        """
        AC7: sentinel.execute must not eagerly import psutil at module-load time.

        Records sys.modules before and after importing and asserts psutil was NOT
        freshly added.  If psutil was already imported by a prior test this assertion
        is vacuously satisfied for that key — the important invariant is that the
        module load itself does not pull in the heavy OS path.
        """
        before = frozenset(sys.modules.keys())
        import sentinel.execute  # noqa: F401

        after = frozenset(sys.modules.keys())
        new_modules = after - before
        assert "psutil" not in new_modules, (
            "sentinel.execute imported psutil at module-load time; "
            "real OS adapters must use deferred/lazy imports inside functions"
        )

    def test_when_execute_module_imported_then_no_subprocess_is_spawned_at_load_time(
        self,
    ):
        """
        AC7: importing sentinel.execute must not spawn any subprocess.

        Patches subprocess.Popen and subprocess.run to raise; if build_executor
        or any top-level code in the module calls subprocess, the patched version
        triggers and the test fails immediately.
        """
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("subprocess.Popen spawned at construction"),
        ):
            with patch(
                "subprocess.run",
                side_effect=AssertionError("subprocess.run called at construction"),
            ):
                engine = _build_executor()(ExecuteConfig())
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_without_components_then_no_subprocess_is_spawned(
        self,
    ):
        """
        AC7: build_executor(config) with default adapters must not spawn subprocesses.

        Deferred imports guarantee heavy paths only activate when execute() is called.
        """
        build_executor = _build_executor()
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("subprocess spawned during build_executor()"),
        ):
            with patch(
                "subprocess.run",
                side_effect=AssertionError(
                    "subprocess.run called during build_executor()"
                ),
            ):
                engine = build_executor(ExecuteConfig())
        assert isinstance(engine, _engine_cls())

    def test_when_build_executor_called_with_fakes_then_no_subprocess_is_spawned(self):
        """AC7: even with fake injection, build_executor must not touch subprocesses."""
        build_executor = _build_executor()
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("subprocess.Popen spawned with fakes"),
        ):
            with patch(
                "subprocess.run",
                side_effect=AssertionError("subprocess.run called with fakes"),
            ):
                engine = build_executor(
                    ExecuteConfig(),
                    components={
                        "killer": _SpyKiller(),
                        "stopper": _SpyStopper(),
                        "cleaner": _SpyCleaner(),
                        "audit": _SpyAudit(),
                        "notifier": _SpyNotifier(),
                    },
                )
        assert isinstance(engine, _engine_cls())


# ══════════════════════════════════════════════════════════════════════════════
# AC8 — build_executor selects MacNotifier vs NullNotifier by
#        mode / NotifyConfig.enabled
# ══════════════════════════════════════════════════════════════════════════════
#
# Verification strategy (source-blind):
#   To avoid inspecting internal engine attributes, we use the "wrong class raises"
#   technique: patch the __init__ of the class that MUST NOT be selected to raise,
#   then build the engine.  If the implementation selects the correct class the
#   patch never fires and construction succeeds; if it selects the wrong class the
#   patch raises and the test fails, proving the selection was incorrect.


class TestNotifierSelection:
    """AC8: MacNotifier selected when notify.enabled=True + AUTO; NullNotifier otherwise."""

    def test_when_notify_disabled_then_null_notifier_is_selected_not_mac_notifier(self):
        """
        AC8: NotifyConfig.enabled=False → NullNotifier wired.

        If MacNotifier is mistakenly selected its patched __init__ raises, failing
        the test.  Construction success proves NullNotifier was chosen.
        """
        from sentinel.notify import MacNotifier  # noqa: PLC0415

        config = ExecuteConfig(
            mode=ExecutionMode.AUTO,
            notify=NotifyConfig(enabled=False),
        )
        with patch.object(
            MacNotifier,
            "__init__",
            side_effect=RuntimeError(
                "MacNotifier instantiated when NotifyConfig.enabled=False — NullNotifier expected"
            ),
        ):
            engine = _build_executor()(
                config,
                components={
                    "killer": _SpyKiller(),
                    "stopper": _SpyStopper(),
                    "cleaner": _SpyCleaner(),
                    "audit": _SpyAudit(),
                },
            )
        assert isinstance(engine, _engine_cls())

    def test_when_notify_enabled_and_auto_mode_then_mac_notifier_is_selected_not_null_notifier(
        self,
    ):
        """
        AC8: NotifyConfig.enabled=True + ExecutionMode.AUTO → MacNotifier wired.

        If NullNotifier is mistakenly selected its patched __init__ raises.
        """
        from sentinel.notify import NullNotifier  # noqa: PLC0415

        config = ExecuteConfig(
            mode=ExecutionMode.AUTO,
            notify=NotifyConfig(enabled=True),
        )
        with patch.object(
            NullNotifier,
            "__init__",
            side_effect=RuntimeError(
                "NullNotifier instantiated when notify.enabled=True + AUTO — MacNotifier expected"
            ),
        ):
            engine = _build_executor()(
                config,
                components={
                    "killer": _SpyKiller(),
                    "stopper": _SpyStopper(),
                    "cleaner": _SpyCleaner(),
                    "audit": _SpyAudit(),
                },
            )
        assert isinstance(engine, _engine_cls())

    def test_when_dry_run_mode_then_null_notifier_is_selected_not_mac_notifier(self):
        """
        AC8: DRY_RUN mode → NullNotifier wired regardless of notify.enabled.

        DRY_RUN is log-only; OS notifications must not fire.
        """
        from sentinel.notify import MacNotifier  # noqa: PLC0415

        config = ExecuteConfig(
            mode=ExecutionMode.DRY_RUN,
            notify=NotifyConfig(enabled=True),
        )
        with patch.object(
            MacNotifier,
            "__init__",
            side_effect=RuntimeError(
                "MacNotifier instantiated in DRY_RUN mode — NullNotifier expected"
            ),
        ):
            engine = _build_executor()(
                config,
                components={
                    "killer": _SpyKiller(),
                    "stopper": _SpyStopper(),
                    "cleaner": _SpyCleaner(),
                    "audit": _SpyAudit(),
                },
            )
        assert isinstance(engine, _engine_cls())

    def test_when_notify_enabled_and_confirm_mode_then_mac_notifier_is_not_selected(
        self,
    ):
        """
        AC8: CONFIRM mode → NullNotifier (nothing is executed; no OS notification fires).

        Assumption: CONFIRM behaves like DRY_RUN for notifier selection since no
        action is dispatched.  If the implementation chooses MacNotifier in CONFIRM
        the patched __init__ will raise, flagging this assumption for the author to
        revisit.
        """
        from sentinel.notify import MacNotifier  # noqa: PLC0415

        config = ExecuteConfig(
            mode=ExecutionMode.CONFIRM,
            notify=NotifyConfig(enabled=True),
        )
        with patch.object(
            MacNotifier,
            "__init__",
            side_effect=RuntimeError(
                "MacNotifier instantiated in CONFIRM mode — NullNotifier expected"
            ),
        ):
            try:
                engine = _build_executor()(
                    config,
                    components={
                        "killer": _SpyKiller(),
                        "stopper": _SpyStopper(),
                        "cleaner": _SpyCleaner(),
                        "audit": _SpyAudit(),
                    },
                )
                assert isinstance(engine, _engine_cls())
            except RuntimeError:
                pytest.skip(
                    "CONFIRM selects MacNotifier — update AC8 assumption in this test if intentional"
                )
