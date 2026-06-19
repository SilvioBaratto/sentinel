"""POSIX signal shims — injected os.kill; never raise into caller.

PosixProcessSignaler: sends a signal; returns False on any failure.
PosixAliveProbe:      probes liveness via kill(pid, 0); PermissionError → True
                      (process exists but belongs to another user).
"""

from __future__ import annotations

import os
from typing import Callable


class PosixProcessSignaler:
    def __init__(self, os_kill: Callable[[int, int], None] = os.kill) -> None:
        self._os_kill = os_kill

    def signal(self, pid: int, sig: int) -> bool:
        try:
            self._os_kill(pid, sig)
            return True
        except Exception:
            return False


class PosixAliveProbe:
    def __init__(self, os_kill: Callable[[int, int], None] = os.kill) -> None:
        self._os_kill = os_kill

    def is_alive(self, pid: int) -> bool:
        try:
            self._os_kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False
