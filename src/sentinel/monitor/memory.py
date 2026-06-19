from __future__ import annotations

import psutil
from typing import Any, Callable

from sentinel.domain.value_objects import MemoryReport

__all__ = ["PsutilMemoryReader"]


class PsutilMemoryReader:
    """Wrap psutil.virtual_memory() into the domain MemoryReport.

    Field mapping (macOS rationale):
      total_bytes  ← vm.total      (total physical RAM)
      used_bytes   ← vm.used       (active + wired; documented-buggy on macOS [3-0])
      free_bytes   ← vm.available  (memory that can be given to processes without swap;
                                    more meaningful than total-used under compression)

    Reporting-only — MUST NOT be used as a state-machine gate. [3-0]
    """

    def __init__(
        self, virtual_memory: Callable[[], Any] = psutil.virtual_memory
    ) -> None:
        self._virtual_memory = virtual_memory

    def read(self) -> MemoryReport:
        vm = self._virtual_memory()
        return MemoryReport(
            total_bytes=vm.total,
            used_bytes=vm.used,
            free_bytes=vm.available,
        )
