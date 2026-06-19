from __future__ import annotations

import subprocess
from typing import Callable

__all__ = ["HidReadError", "IoregHidIdleReader"]

_NS_PER_SECOND = 1_000_000_000
_IOREG_ARGS = ["ioreg", "-c", "IOHIDSystem"]


class HidReadError(Exception):
    """Raised when ioreg output cannot be parsed to a HIDIdleTime value.

    Fail-safe contract: callers must treat this as not-idle (0.0), never crash.
    """


def _default_ioreg_runner() -> str:
    return subprocess.check_output(_IOREG_ARGS, text=True)


class IoregHidIdleReader:
    """Parse HIDIdleTime (nanoseconds) from ioreg output → seconds.

    The ioreg callable is injected so tests pass a canned string; no subprocess
    is spawned in pytest.
    """

    def __init__(
        self,
        ioreg_runner: Callable[[], str] = _default_ioreg_runner,
    ) -> None:
        self._ioreg_runner = ioreg_runner

    def read(self) -> float:
        return self._parse(self._ioreg_runner())

    def _parse(self, raw: str) -> float:
        for line in raw.splitlines():
            if "HIDIdleTime" not in line:
                continue
            try:
                ns = int(line.split("=")[-1].strip())
                return float(ns / _NS_PER_SECOND)
            except ValueError:
                continue
        raise HidReadError("HIDIdleTime not found in ioreg output")
