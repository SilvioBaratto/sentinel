"""ExecutionEngine — routes DetectionResult to executors by state + mode.

AUTO:    execute each action; audit + notify per result.
DRY_RUN: synthesise ActionResult(dry_run=True); audit "would …"; never invoke real executor.
CONFIRM: enqueue planned ActionResult; audit "queued …"; nothing executed; drain via pending().

State gate:
  NORMAL           → empty tuple; no executor called.
  WARN / CRITICAL  → killer + stopper; cleaner NOT called.
  DISK_LOW         → killer + stopper + cleaner (AUTO only).

Error isolation: one executor raising does not abort the batch; engine never propagates.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable

from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    AuditRecord,
    ContainerCandidate,
    DetectionResult,
    ExecutionMode,
    ProcessCandidate,
    Reversibility,
    SentinelState,
)


# ── Module-level helpers (pure, no IO) ────────────────────────────────────────


def _kill_plan(c: ProcessCandidate) -> ActionResult:
    return ActionResult(
        kind=ActionKind.KILL_PROCESS,
        target=c.info.name,
        success=True,
        reversibility=Reversibility.PERMANENT,
    )


def _stop_plan(c: ContainerCandidate) -> ActionResult:
    return ActionResult(
        kind=ActionKind.STOP_CONTAINER,
        target=c.name,
        success=True,
        reversibility=Reversibility.REVERSIBLE,
    )


def _make_audit(
    r: ActionResult, mode: ExecutionMode, ts: float, detail: str
) -> AuditRecord:
    return AuditRecord(
        timestamp=ts,
        kind=r.kind,
        target=r.target,
        success=r.success,
        reversibility=r.reversibility,
        bytes_freed=r.bytes_freed,
        mode=mode,
        detail=detail,
    )


# ── ExecutionEngine ───────────────────────────────────────────────────────────


class ExecutionEngine:
    """Orchestrates execution across all three executor types per mode and state."""

    def __init__(
        self,
        killer,
        stopper,
        cleaner,
        audit,
        notifier,
        mode: ExecutionMode,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._killer = killer
        self._stopper = stopper
        self._cleaner = cleaner
        self._audit = audit
        self._notifier = notifier
        self._mode = mode
        self._clock: Callable[[], float] = clock or time.time
        self._pending: list[ActionResult] = []

    def execute(
        self, detection: DetectionResult, state: SentinelState
    ) -> tuple[ActionResult, ...]:
        if state == SentinelState.NORMAL:
            return ()
        self._pending.clear()
        return (
            *self._run_kills(detection.processes),
            *self._run_stops(detection.containers),
            *self._run_disk(state),
        )

    def pending(self) -> tuple[ActionResult, ...]:
        return tuple(self._pending)

    # ── per-type runners ──────────────────────────────────────────────────────

    def _run_kills(self, candidates: tuple) -> tuple[ActionResult, ...]:
        return tuple(
            r
            for c in candidates
            if (r := self._dispatch(lambda p=c: self._killer.kill(p), _kill_plan(c)))
            is not None
        )

    def _run_stops(self, candidates: tuple) -> tuple[ActionResult, ...]:
        return tuple(
            r
            for c in candidates
            if (r := self._dispatch(lambda s=c: self._stopper.stop(s), _stop_plan(c)))
            is not None
        )

    def _run_disk(self, state: SentinelState) -> tuple[ActionResult, ...]:
        if state != SentinelState.DISK_LOW or self._mode != ExecutionMode.AUTO:
            return ()
        try:
            results = self._cleaner.clean(state)
            for r in results:
                self._record(r, r.detail)
                self._notifier.notify(r)
            return results
        except Exception:
            return ()

    # ── mode dispatch ─────────────────────────────────────────────────────────

    def _dispatch(self, fn: Callable, plan: ActionResult) -> ActionResult | None:
        if self._mode == ExecutionMode.DRY_RUN:
            return self._dry(plan)
        if self._mode == ExecutionMode.CONFIRM:
            return self._queue(plan)
        return self._auto(fn, plan)

    def _dry(self, plan: ActionResult) -> ActionResult:
        r = replace(plan, dry_run=True)
        self._record(r, f"would {r.kind.value} {r.target}")
        return r

    def _queue(self, plan: ActionResult) -> None:
        self._pending.append(plan)
        self._record(plan, f"queued {plan.kind.value} {plan.target}")
        return None

    def _auto(self, fn: Callable, plan: ActionResult) -> ActionResult:
        try:
            r = fn()
        except Exception as exc:
            r = replace(plan, success=False, detail=str(exc))
        self._record(r, r.detail)
        self._notifier.notify(r)
        return r

    # ── audit helper ──────────────────────────────────────────────────────────

    def _record(self, r: ActionResult, detail: str) -> None:
        try:
            self._audit.record(_make_audit(r, self._mode, self._clock(), detail))
        except Exception:
            pass
