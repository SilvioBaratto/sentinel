"""Graceful-quit shims.

NSRunningAppQuitter — graceful-quit a GUI app BY PID via Cocoa
    ``NSRunningApplication.terminate()`` (same as ⌘-Q: lets the app save).  No
    process name is interpolated into any command, so there is no AppleScript or
    shell injection surface, and there is no name="" no-op trap.  This is the
    prod kill path (quit_sender(pid)).

OsascriptAppQuitter — legacy AppleScript-by-name quitter, kept as the offline
    fallback when pyobjc/AppKit is unavailable.

Both fail-safe: any exception → False (never raises into caller).  Neither
confirms exit; the kill engine verifies separately.
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


# ── PID-based graceful quit (no name interpolation) ──────────────────────────


def _terminate_by_pid(pid: int) -> bool:
    """Ask the GUI app owning *pid* to quit via NSRunningApplication.terminate().

    Deferred AppKit import keeps pyobjc optional. Returns False when *pid* is not
    a running GUI application (so the caller falls back / escalates).
    """
    from AppKit import NSRunningApplication  # noqa: PLC0415 — deferred; pyobjc optional  # type: ignore[import-untyped]

    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    return bool(app.terminate())


def _osascript_quit_by_pid(pid: int) -> bool:
    """Fallback: resolve *pid* → app name (psutil) and quit via osascript.

    Uses an argv list (never a shell) and escapes the name for the AppleScript
    string literal, so a hostile process name cannot inject AppleScript or shell.
    """
    import psutil  # noqa: PLC0415 — deferred

    name = psutil.Process(pid).name()
    safe = name.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'tell application "{safe}" to quit'],
        capture_output=True,
        check=False,
    )
    return True


class NSRunningAppQuitter:
    """Graceful-quit a GUI app by PID; falls back to name-based quit on failure."""

    def __init__(
        self,
        terminate: Callable[[int], bool] = _terminate_by_pid,
        fallback: Callable[[int], bool] = _osascript_quit_by_pid,
    ) -> None:
        self._terminate = terminate
        self._fallback = fallback

    def quit(self, pid: int) -> bool:
        try:
            if self._terminate(pid):
                return True
        except Exception:
            pass
        try:
            return bool(self._fallback(pid))
        except Exception:
            return False
