from __future__ import annotations

import psutil
from typing import Any, Callable

from sentinel.domain.value_objects import DiskUsage

__all__ = ["DiskUsage", "PsutilDiskReader"]


class PsutilDiskReader:
    """Read per-volume disk usage via psutil, returning a frozen DiskUsage."""

    def __init__(self, disk_usage: Callable[[str], Any] = psutil.disk_usage) -> None:
        self._disk_usage = disk_usage

    def read(self, mount: str) -> DiskUsage:
        info = self._disk_usage(mount)
        return DiskUsage(
            mount=mount,
            free_bytes=info.free,
            total_bytes=info.total,
        )
