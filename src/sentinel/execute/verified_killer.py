"""VerifiedKiller — quit → SIGTERM → SIGKILL with verify-exit at each step.

Implements Killer: kill(candidate) -> ActionResult.
All OS calls are injected; kill() never raises — errors yield outcome=ERROR.

PID-reuse guard: before each escalation, if the caller supplies a
create_time_reader, the current process create_time is compared against the
original recorded in the candidate.  A mismatch means the pid was recycled by
a different process — treat that as EXITED and stop escalating.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from typing import Callable

from sentinel.config import KillConfig
from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    KillOutcome,
    KillStage,
    Reversibility,
)

_SIGTERM = signal.SIGTERM
_SIGKILL = signal.SIGKILL

_CRITICAL_PRESSURE = 4


# ── Internal context passed between phase helpers ────────────────────────────


@dataclass(frozen=True)
class _KillCtx:
    pid: int
    name: str
    original_ct: float | None
    pressure: int


# ── Candidate field extractors (duck-typed; work for both real and fake) ─────


def _get_pid(candidate) -> int:
    try:
        return candidate.info.pid
    except AttributeError:
        return candidate.pid


def _get_name(candidate) -> str:
    try:
        return candidate.info.name
    except AttributeError:
        return candidate.name


def _get_create_time(candidate) -> float | None:
    try:
        return candidate.info.create_time
    except AttributeError:
        return getattr(candidate, "create_time", None)


def _get_pressure(candidate) -> int:
    return getattr(candidate, "pressure_level", 2)


def _safe_name(candidate) -> str:
    try:
        return _get_name(candidate)
    except Exception:
        return "<unknown>"


def _extract(candidate) -> _KillCtx:
    return _KillCtx(
        pid=_get_pid(candidate),
        name=_get_name(candidate),
        original_ct=_get_create_time(candidate),
        pressure=_get_pressure(candidate),
    )


# ── Result factory ────────────────────────────────────────────────────────────


def _result(outcome: KillOutcome, stage: KillStage, target: str) -> ActionResult:
    return ActionResult(
        kind=ActionKind.KILL_PROCESS,
        target=target,
        success=outcome == KillOutcome.EXITED,
        reversibility=Reversibility.PERMANENT,
        outcome=outcome,
        stage=stage,
    )


# ── VerifiedKiller ────────────────────────────────────────────────────────────


class VerifiedKiller:
    """Escalate graceful quit → SIGTERM → SIGKILL, verifying exit at each step.

    Injected callables keep all OS interaction out of this class.
    create_time_reader enables the PID-reuse guard; omit it to skip the guard.
    """

    def __init__(
        self,
        config: KillConfig,
        *,
        quit_sender: Callable[[int], None],
        alive_checker: Callable[[int], bool],
        signal_sender: Callable[[int, int], None],
        sleeper: Callable[[float], None],
        create_time_reader: Callable[[int], float | None] = lambda pid: None,
    ) -> None:
        self._config = config
        self._quit_sender = quit_sender
        self._alive_checker = alive_checker
        self._signal_sender = signal_sender
        self._sleeper = sleeper
        self._create_time_reader = create_time_reader

    # ── Public ───────────────────────────────────────────────────────────────

    def kill(self, candidate) -> ActionResult:
        try:
            return self._do_kill(candidate)
        except Exception:
            return _result(KillOutcome.ERROR, KillStage.NONE, _safe_name(candidate))

    # ── Orchestration ─────────────────────────────────────────────────────────

    def _do_kill(self, candidate) -> ActionResult:
        ctx = _extract(candidate)
        if not self._quit_phase(ctx):
            return _result(KillOutcome.EXITED, KillStage.QUIT, ctx.name)
        if not self._sigterm_phase(ctx):
            return _result(KillOutcome.EXITED, KillStage.SIGTERM, ctx.name)
        return self._post_sigterm(ctx)

    def _quit_phase(self, ctx: _KillCtx) -> bool:
        self._quit_sender(ctx.pid)
        self._sleeper(self._quit_grace(ctx.name, ctx.pressure))
        return self._is_alive(ctx.pid, ctx.original_ct)

    def _sigterm_phase(self, ctx: _KillCtx) -> bool:
        self._signal_sender(ctx.pid, _SIGTERM)
        self._sleeper(self._config.sigterm_grace_seconds)
        return self._is_alive(ctx.pid, ctx.original_ct)

    def _post_sigterm(self, ctx: _KillCtx) -> ActionResult:
        if self._is_editor(ctx.name) and not self._config.editor_auto_sigkill:
            return _result(KillOutcome.SURVIVED, KillStage.SIGTERM, ctx.name)
        self._signal_sender(ctx.pid, _SIGKILL)
        return _result(KillOutcome.EXITED, KillStage.SIGKILL, ctx.name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _quit_grace(self, name: str, pressure: int) -> float:
        if pressure == _CRITICAL_PRESSURE:
            return self._config.critical_quit_grace_seconds
        if self._is_editor(name):
            return self._config.editor_quit_grace_seconds
        return self._config.quit_grace_seconds

    def _is_alive(self, pid: int, original_ct: float | None) -> bool:
        if not self._alive_checker(pid):
            return False
        if original_ct is None:
            return True
        current_ct = self._create_time_reader(pid)
        if current_ct is not None and current_ct != original_ct:
            return False  # pid recycled by a different process
        return True

    def _is_editor(self, name: str) -> bool:
        return name in self._config.editor_names
