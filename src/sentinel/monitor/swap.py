from __future__ import annotations

import re
import subprocess
from typing import Callable

from sentinel.domain.value_objects import SwapUsage

__all__ = ["SysctlSwapReader"]

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024
_MULTIPLIER = {"M": _MB, "G": _GB}

# Matches: total = 1024.00M  used = 0.00M  free = 1024.00M  (with M or G suffix)
_PATTERN = re.compile(
    r"total\s*=\s*(\d+(?:\.\d+)?)([MG])\s+"
    r"used\s*=\s*(\d+(?:\.\d+)?)([MG])\s+"
    r"free\s*=\s*(\d+(?:\.\d+)?)([MG])"
)


def _default_sysctl() -> str:
    return subprocess.check_output(["sysctl", "vm.swapusage"], text=True)


class SysctlSwapReader:
    """Parse sysctl vm.swapusage output into a SwapUsage value object."""

    def __init__(self, sysctl: Callable[[], str] = _default_sysctl) -> None:
        self._sysctl = sysctl

    def read(self) -> SwapUsage:
        return self._parse(self._sysctl())

    def _parse(self, raw: str) -> SwapUsage:
        m = _PATTERN.search(raw)
        if not m:
            raise ValueError(f"cannot parse vm.swapusage output: {raw!r}")
        return SwapUsage(
            total_bytes=_to_bytes(m.group(1), m.group(2)),
            used_bytes=_to_bytes(m.group(3), m.group(4)),
            free_bytes=_to_bytes(m.group(5), m.group(6)),
        )


def _to_bytes(value: str, suffix: str) -> int:
    return round(float(value) * _MULTIPLIER[suffix])
