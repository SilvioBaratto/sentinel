from __future__ import annotations

import time
from typing import Callable

import psutil

from sentinel.config import ProcessConfig
from sentinel.domain.value_objects import ProcessInfo

__all__ = ["PsutilProcessLister"]


def _safe_pgid(proc) -> int | None:
    try:
        return proc.pgid()
    except Exception:
        return None


def _build_info(
    proc, cpu: float, create_time: float | None, idle_secs: float
) -> ProcessInfo:
    tty = proc.terminal()
    return ProcessInfo(
        pid=proc.pid,
        ppid=proc.ppid(),
        name=proc.name(),
        cmdline=tuple(proc.cmdline()),
        has_tty=tty is not None,
        tty=tty,
        pgid=_safe_pgid(proc),
        cpu_percent=cpu,
        rss_bytes=proc.memory_info().rss,
        create_time=create_time,
        idle_seconds=idle_secs,
    )


def _default_process_iter():
    return psutil.process_iter()


class PsutilProcessLister:
    """Snapshot all running processes into ProcessInfo tuples.

    Two-pass CPU sampling (non-blocking, O(1) wall-clock):
      1. Prime every process counter with cpu_percent(interval=None).
      2. Sleep once for cpu_sample_interval.
      3. Read each counter again — the delta is the sustained CPU %.

    Per-pid idle tracking (persistent across calls):
      idle_seconds = time since this pid's CPU first dropped below idle_cpu_percent,
      guarded by create_time to detect pid reuse.

    All OS callables are injected so tests run without real psutil or sleep.
    """

    def __init__(
        self,
        process_iter: Callable = _default_process_iter,
        sleep: Callable[[float], None] = time.sleep,
        config: ProcessConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._process_iter = process_iter
        self._sleep = sleep
        self._config = config or ProcessConfig()
        self._clock = clock
        # {pid: (idle_since_monotonic, create_time)}
        self._idle_state: dict[int, tuple[float, float | None]] = {}

    def list(self) -> tuple[ProcessInfo, ...]:
        primed = self._prime_all()
        self._sleep(self._config.cpu_sample_interval)
        results = tuple(filter(None, (self._read(p) for p in primed)))
        self._prune_idle_state({r.pid for r in results})
        return results

    def _prime_all(self) -> list:
        primed = []
        for proc in self._process_iter():
            try:
                proc.cpu_percent(interval=None)
                primed.append(proc)
            except Exception:
                pass
        return primed

    def _read(self, proc) -> ProcessInfo | None:
        try:
            cpu = proc.cpu_percent(interval=None)
            create_time = proc.create_time()
            idle_secs = self._get_idle_seconds(proc.pid, create_time, cpu)
            return _build_info(proc, cpu, create_time, idle_secs)
        except Exception:
            return None

    def _get_idle_seconds(
        self, pid: int, create_time: float | None, cpu: float
    ) -> float:
        now = self._clock()
        if cpu >= self._config.idle_cpu_percent:
            self._idle_state.pop(pid, None)
            return 0.0
        state = self._idle_state.get(pid)
        if state is not None and state[1] == create_time:
            return now - state[0]
        self._idle_state[pid] = (now, create_time)
        return 0.0

    def _prune_idle_state(self, active_pids: set[int]) -> None:
        for pid in list(self._idle_state.keys()):
            if pid not in active_pids:
                del self._idle_state[pid]
