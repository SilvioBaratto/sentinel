from __future__ import annotations

import re
import subprocess
from typing import Callable

from sentinel.domain.value_objects import FrontmostApp

__all__ = [
    "LsappinfoFrontmostReader",
    "NSWorkspaceFrontmostReader",
    "make_frontmost_reader",
]

_BUNDLE_RE = re.compile(r'bundleID="([^"]+)"')
_NAME_RE = re.compile(r'\bname="([^"]+)"')
_PID_RE = re.compile(r"\bpid=\s*(\d+)")


def _default_os_runner() -> str:
    return subprocess.check_output(["lsappinfo", "front"], text=True)


def _match_str(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def _match_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    return int(m.group(1)) if m else None


class LsappinfoFrontmostReader:
    """Parse lsappinfo front output → FrontmostApp.

    OS callable is injected so tests use canned text; no subprocess in pytest.
    Fail-safe: any error or unresolvable output → FrontmostApp(None, None, None).
    """

    def __init__(self, os_runner: Callable[[], str] = _default_os_runner) -> None:
        self._os_runner = os_runner

    def read(self) -> FrontmostApp:
        try:
            return self._parse(self._os_runner())
        except Exception:
            return FrontmostApp(None, None, None)

    def _parse(self, raw: str) -> FrontmostApp:
        return FrontmostApp(
            bundle_id=_match_str(_BUNDLE_RE, raw),
            name=_match_str(_NAME_RE, raw),
            pid=_match_int(_PID_RE, raw),
        )


class NSWorkspaceFrontmostReader:
    """Opt-in frontmost reader using NSWorkspace.sharedWorkspace().frontmostApplication().

    pyobjc (AppKit) is imported inside read() — deferred so that importing this
    module never requires pyobjc installed. DETECTION ONLY: no mutating calls.
    """

    def read(self) -> FrontmostApp:
        try:
            return self._read_nsworkspace()
        except Exception:
            return FrontmostApp(None, None, None)

    def _read_nsworkspace(self) -> FrontmostApp:
        from AppKit import NSWorkspace  # noqa: PLC0415 — deferred; pyobjc optional  # type: ignore[import-untyped]

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return FrontmostApp(None, None, None)
        return FrontmostApp(
            bundle_id=app.bundleIdentifier(),
            name=app.localizedName(),
            pid=app.processIdentifier(),
        )


def make_frontmost_reader(
    use_nsworkspace_frontmost: bool = False,
) -> LsappinfoFrontmostReader | NSWorkspaceFrontmostReader:
    """Factory: lsappinfo (no-TCC default) or NSWorkspace (opt-in)."""
    if use_nsworkspace_frontmost:
        return NSWorkspaceFrontmostReader()
    return LsappinfoFrontmostReader()
