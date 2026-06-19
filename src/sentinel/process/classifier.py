from __future__ import annotations

from typing import Mapping

from sentinel.config import ProcessConfig
from sentinel.domain.value_objects import (
    ProcessClassification,
    ProcessInfo,
    ProcessProtection,
)
from sentinel.process.walker import TtyLineageWalker

__all__ = ["DefaultProcessClassifier"]


class DefaultProcessClassifier:
    """Implements ProcessClassifier protocol.

    Check order (first match wins):
      1. Never-kill list  → PROTECTED
      2. TTY / shell lineage walk → PROTECTED
      3. Reap allow-list  → REAPABLE
      4. Default          → PROTECTED   (protect-on-ambiguity)
    """

    def __init__(self, config: ProcessConfig | None = None) -> None:
        cfg = config or ProcessConfig()
        self._protected: frozenset[str] = frozenset(cfg.protected_names)
        self._reapable: frozenset[str] = frozenset(cfg.reap_allow_list)
        self._walker = TtyLineageWalker(frozenset(cfg.shell_session_markers))

    def classify(
        self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]
    ) -> ProcessClassification:
        try:
            protection, reason = self._decide(proc, index)
        except Exception:
            protection = ProcessProtection.PROTECTED
            reason = "protect-on-ambiguity: exception during classification"
        return ProcessClassification(
            pid=proc.pid, name=proc.name, protection=protection, reason=reason
        )

    def _decide(
        self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]
    ) -> tuple[ProcessProtection, str]:
        if proc.name in self._protected:
            return ProcessProtection.PROTECTED, f"never-kill list: {proc.name}"
        if self._walker.walk(proc, index):
            return ProcessProtection.PROTECTED, "TTY or shell ancestry detected"
        if proc.name in self._reapable:
            return ProcessProtection.REAPABLE, f"reap allow-list: {proc.name}"
        return ProcessProtection.PROTECTED, "no reap rule matched — protect by default"
