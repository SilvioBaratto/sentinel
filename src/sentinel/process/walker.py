from __future__ import annotations

from typing import Mapping

from sentinel.domain.value_objects import ProcessInfo

__all__ = ["TtyLineageWalker"]

_DEFAULT_SHELL_MARKERS: frozenset[str] = frozenset(
    {
        "login",
        "-zsh",
        "-bash",
        "zsh",
        "bash",
        "Terminal",
        "iTerm2",
        "tmux",
        "screen",
        "ssh",
    }
)


class TtyLineageWalker:
    """Pure — no OS calls.  Walk ppid chain and pgid membership via an injected index.

    Protect-on-ambiguity: any unhandled exception inside walk() returns True.
    """

    def __init__(self, markers: frozenset[str] | None = None) -> None:
        self._markers = markers if markers is not None else _DEFAULT_SHELL_MARKERS

    def walk(self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]) -> bool:
        """Return True if proc has a TTY, a shell ancestor, or a shell pgid mate."""
        try:
            return (
                self._has_tty(proc)
                or self._has_shell_ancestor(proc, index)
                or self._shares_pgid_with_shell(proc, index)
            )
        except Exception:
            return True

    def _has_tty(self, proc: ProcessInfo) -> bool:
        return proc.has_tty

    def _has_shell_ancestor(
        self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]
    ) -> bool:
        visited: set[int] = set()
        current_pid = proc.ppid
        while current_pid in index and current_pid not in visited:
            visited.add(current_pid)
            ancestor = index[current_pid]
            if self._is_shell(ancestor):
                return True
            current_pid = ancestor.ppid
        return False

    def _shares_pgid_with_shell(
        self, proc: ProcessInfo, index: Mapping[int, ProcessInfo]
    ) -> bool:
        if proc.pgid is None:
            return False
        return any(
            p.pgid == proc.pgid and self._is_shell(p)
            for p in index.values()
            if p.pid != proc.pid
        )

    def _is_shell(self, proc: ProcessInfo) -> bool:
        if proc.name in self._markers:
            return True
        return bool(proc.cmdline) and proc.cmdline[0] in self._markers
