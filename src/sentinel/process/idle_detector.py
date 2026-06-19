from __future__ import annotations

import time
from typing import Callable

from sentinel.config import ProcessConfig
from sentinel.domain.protocols import (
    FrontmostAppReader,
    HidIdleReader,
    ProcessClassifier,
    ProcessLister,
)
from sentinel.domain.value_objects import (
    FrontmostApp,
    ProcessCandidate,
    ProcessClassification,
    ProcessInfo,
    ProcessProtection,
    SentinelState,
)

__all__ = ["DefaultProcessIdleDetector"]


class DefaultProcessIdleDetector:
    """Orchestrate per-process idle detection — pure composition, no OS calls.

    Gate: returns () immediately when state is NORMAL without touching any reader.
    Protect-on-ambiguity: any reader exception drops that process from the list;
    the detector never propagates exceptions into the pipeline.
    """

    def __init__(
        self,
        lister: ProcessLister,
        frontmost_reader: FrontmostAppReader,
        hid_reader: HidIdleReader,
        classifier: ProcessClassifier,
        config: ProcessConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lister = lister
        self._frontmost_reader = frontmost_reader
        self._hid_reader = hid_reader
        self._classifier = classifier
        self._config = config or ProcessConfig()
        self._clock = clock

    def detect(self, state: SentinelState) -> tuple[ProcessCandidate, ...]:
        if state == SentinelState.NORMAL:
            return ()
        procs = self._lister.list()
        index: dict[int, ProcessInfo] = {p.pid: p for p in procs}
        frontmost = self._safe_frontmost()
        hid_idle = self._safe_hid()
        return tuple(
            filter(None, (self._evaluate(p, index, frontmost, hid_idle) for p in procs))
        )

    def _safe_frontmost(self) -> FrontmostApp:
        try:
            return self._frontmost_reader.read()
        except Exception:
            return FrontmostApp(None, None, None)

    def _safe_hid(self) -> float:
        try:
            return self._hid_reader.read()
        except Exception:
            return 0.0

    def _evaluate(
        self,
        proc: ProcessInfo,
        index: dict[int, ProcessInfo],
        frontmost: FrontmostApp,
        hid_idle: float,
    ) -> ProcessCandidate | None:
        try:
            cls = self._classifier.classify(proc, index)
        except Exception:
            return None
        if not self._is_candidate(proc, cls, frontmost, hid_idle):
            return None
        eff = max(proc.idle_seconds, hid_idle)
        return ProcessCandidate(
            info=proc,
            idle_seconds=eff,
            cpu_percent=proc.cpu_percent,
            reason=self._reason(proc, eff),
        )

    def _is_candidate(
        self,
        proc: ProcessInfo,
        cls: ProcessClassification,
        frontmost: FrontmostApp,
        hid_idle: float,
    ) -> bool:
        return (
            cls.protection == ProcessProtection.REAPABLE
            and not self._is_frontmost(proc, frontmost)
            and proc.cpu_percent < self._config.idle_cpu_percent
            and max(proc.idle_seconds, hid_idle) > self._config.idle_seconds
        )

    def _is_frontmost(self, proc: ProcessInfo, frontmost: FrontmostApp) -> bool:
        if frontmost.pid is not None:
            return proc.pid == frontmost.pid
        if frontmost.name is not None:
            return proc.name == frontmost.name
        return True  # unresolved → protect

    def _reason(self, proc: ProcessInfo, idle_secs: float) -> str:
        h, m = int(idle_secs // 3600), int((idle_secs % 3600) // 60)
        return (
            f"{proc.name} idle {h}h{m:02d}m, cpu {proc.cpu_percent:.1f}%, not frontmost"
        )
