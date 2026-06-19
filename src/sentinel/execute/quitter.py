"""OsascriptAppQuitter — graceful-quit via AppleScript, injected os_runner.

Fail-safe: any exception → False (never raises into caller).
Does NOT confirm exit; the kill engine verifies separately.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Callable


def _default_osascript(cmd: str) -> None:
    subprocess.run(shlex.split(cmd), capture_output=True, check=False)


class OsascriptAppQuitter:
    def __init__(self, os_runner: Callable[[str], object] = _default_osascript) -> None:
        self._os_runner = os_runner

    def quit(self, pid: int, name: str) -> bool:
        try:
            self._os_runner(f"osascript -e 'tell application \"{name}\" to quit'")
            return True
        except Exception:
            return False
