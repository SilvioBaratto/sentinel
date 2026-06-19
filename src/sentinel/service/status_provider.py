"""DefaultStatusProvider — assembles StatusReport from disk seams + one-shot sampler.

No daemon memory is accessed.  The two on-disk seams are:
  - audit_log_path  : rotating key=value log written by RotatingAuditLogger
  - snapshot_path   : JSON state snapshot written by the daemon each tick

One fresh sampler.sample() call provides live pressure + memory + swap + disk.
One detection.detect(state) call provides the current idle candidates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    Reversibility,
    SentinelState,
    StatusReport,
)

# Matches the format RotatingAuditLogger._format() produces:
#   target={target} size={size} reversibility={rev} mode={mode} success={ok}
# The size field may contain a space (e.g. "1.2 GB"), so non-greedy capture is used.
_AUDIT_RE = re.compile(
    r"target=(?P<target>.+?)\s+size=.+?\s+"
    r"reversibility=(?P<rev>\w+)\s+mode=\w+\s+success=(?P<ok>\w+)"
)

_STATE_MAP: dict[str, SentinelState] = {s.value: s for s in SentinelState}


class _SnapshotReader:
    """Read JSON state snapshot; returns defaults on any error."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> tuple[SentinelState, tuple[str, ...]]:
        raw = self._load()
        state = self._parse_state(raw.get("state", ""))
        proxies = tuple(str(p) for p in raw.get("wake_proxies", []))
        return state, proxies

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _parse_state(value: str) -> SentinelState:
        return _STATE_MAP.get(str(value).lower(), SentinelState.NORMAL)


class _AuditParser:
    """Read last N lines of the audit log and parse each into an ActionResult."""

    def __init__(self, path: Path, tail_n: int) -> None:
        self._path = path
        self._tail_n = tail_n

    def read(self) -> tuple[ActionResult, ...]:
        lines = self._tail_lines()
        parsed = (_parse_audit_line(line) for line in lines)
        return tuple(r for r in parsed if r is not None)

    def _tail_lines(self) -> list[str]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        non_empty = [ln for ln in text.splitlines() if ln.strip()]
        return (
            non_empty[-self._tail_n :] if len(non_empty) > self._tail_n else non_empty
        )


def _parse_audit_line(line: str) -> ActionResult | None:
    m = _AUDIT_RE.search(line)
    if not m:
        return None
    return ActionResult(
        kind=ActionKind.STOP_CONTAINER,
        target=m.group("target"),
        success=m.group("ok").lower() == "true",
        reversibility=_parse_reversibility(m.group("rev")),
    )


def _parse_reversibility(value: str) -> Reversibility:
    try:
        return Reversibility(value.lower())
    except ValueError:
        return Reversibility.PERMANENT


class DefaultStatusProvider:
    """Assembles a StatusReport without accessing daemon memory.

    Reads from two disk seams (audit log + state snapshot), calls the sampler
    once for live metrics, and calls detection once for idle candidates.
    Implements the StatusProvider protocol.
    """

    def __init__(
        self,
        sampler: Any,
        detection: Any,
        snapshot_path: Path,
        audit_log_path: Path,
        tail_n: int = 20,
    ) -> None:
        self._sampler = sampler
        self._detection = detection
        self._snap = _SnapshotReader(Path(snapshot_path))
        self._audit = _AuditParser(Path(audit_log_path), tail_n)

    def build(self) -> StatusReport:
        sample = self._sampler.sample()
        state, wake_proxies = self._snap.read()
        detection = self._detection.detect(state)
        return StatusReport(
            pressure=sample.pressure,
            state=state,
            memory=sample.memory,
            swap=sample.swap,
            disks=tuple(sample.disks),
            recent_actions=self._audit.read(),
            idle_processes=tuple(detection.processes),
            idle_containers=tuple(detection.containers),
            wake_proxies=wake_proxies,
        )
