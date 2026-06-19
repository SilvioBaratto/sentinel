from __future__ import annotations

import subprocess
from typing import Callable

from sentinel.domain.value_objects import PressureLevel

__all__ = ["PressureLevel", "PressureReadError", "SysctlPressureReader"]


class PressureReadError(Exception):
    """Raised when sysctl output cannot be parsed to a valid PressureLevel.

    Fail-safe contract: callers must treat this as no-change, never as CRITICAL.
    """


def _default_sysctl() -> str:
    return subprocess.check_output(
        ["sysctl", "kern.memorystatus_vm_pressure_level"], text=True
    )


class SysctlPressureReader:
    """Map sysctl kern.memorystatus_vm_pressure_level output → PressureLevel.

    The OS callable is injected so tests run without a real subprocess.
    """

    def __init__(self, sysctl: Callable[[], str] = _default_sysctl) -> None:
        self._sysctl = sysctl

    def read(self) -> PressureLevel:
        return self._parse(self._sysctl())

    def _parse(self, raw: str) -> PressureLevel:
        text = raw.strip()
        if not text:
            raise PressureReadError("empty sysctl output")
        # Handles "kern.memorystatus_vm_pressure_level: N" and bare "N".
        token = text.rsplit(":", maxsplit=1)[-1].strip()
        try:
            return PressureLevel(int(token))
        except ValueError:
            raise PressureReadError(f"unrecognised pressure level: {token!r}")
