"""DefaultContainerIdleDetector — detects idle Docker containers across N polls + 2h.

Idle criteria (all must hold):
  - CPU < DockerConfig.idle_cpu_percent (default 0.5%)
  - NET I/O and BLOCK I/O deltas ≤ DockerConfig.io_delta_epsilon (default 0)
  - N=DockerConfig.consecutive_polls consecutive idle polls (default 3)
  - clock.now() - idle_since ≥ DockerConfig.idle_seconds (default 7200s / 2h)

Safety gates (checked before any other logic):
  - SentinelState.NORMAL → return () without touching the reader
  - always-up containers (optimizer_* / *_db) → never emitted
  - active exec/attach session → sticky in-use, never emitted

Reader contract (duck-typed):
  - list_containers() → list[str]
  - get_stats(name: str) → dict  # keys: cpu_percent, net_io_delta, block_io_delta, exec_active

Fail-safe: any reader exception → return () or skip that container; never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.config import DockerConfig
from sentinel.docker.allow_list import ContainerAllowList
from sentinel.domain.protocols import Clock, ContainerStatsProvider
from sentinel.domain.value_objects import ContainerCandidate, SentinelState


@dataclass
class _ContainerTrack:
    """Mutable per-container state: consecutive idle counter + idle-since timestamp.

    idle_since is anchored to clock.now() at the *first* idle observation
    (consecutive 0→1 transition) and reset on any non-idle poll.
    """

    consecutive: int = 0
    idle_since: float = 0.0


def _reason(name: str, idle_s: float, cpu: float, n: int) -> str:
    h = int(idle_s // 3600)
    return f"{name} idle {h}h, cpu {cpu:.1f}%, net/block flat over {n} polls"


class DefaultContainerIdleDetector:
    """Compose stats reader, session/exec guard, allow-list, and clock to emit candidates."""

    def __init__(
        self,
        reader: ContainerStatsProvider,
        clock: Clock,
        config: DockerConfig | None = None,
        n_polls: int | None = None,
        allow_list: ContainerAllowList | None = None,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._cfg = config or DockerConfig()
        self._n = n_polls if n_polls is not None else self._cfg.consecutive_polls
        self._al = allow_list or ContainerAllowList(self._cfg)
        self._tracks: dict[str, _ContainerTrack] = {}

    def detect(self, state: SentinelState) -> tuple[ContainerCandidate, ...]:
        if state == SentinelState.NORMAL:
            return ()
        return self._poll()

    # ── internals ─────────────────────────────────────────────────────────────

    def _poll(self) -> tuple[ContainerCandidate, ...]:
        try:
            names = self._reader.list_containers()
        except Exception:
            return ()
        return tuple(filter(None, (self._evaluate(name) for name in names)))

    def _evaluate(self, name: str) -> ContainerCandidate | None:
        try:
            stats = self._reader.get_stats(name)
        except Exception:
            return None
        if self._al.is_always_up(name) or stats.get("exec_active"):
            return None
        return self._update(name, stats)

    def _update(self, name: str, stats: dict) -> ContainerCandidate | None:
        track = self._tracks.setdefault(name, _ContainerTrack())
        if not self._is_idle(stats):
            track.consecutive, track.idle_since = 0, self._clock.now()
            return None
        if track.consecutive == 0:
            track.idle_since = self._clock.now()
        track.consecutive += 1
        return self._emit(name, stats, track) if self._is_emittable(track) else None

    def _is_idle(self, stats: dict) -> bool:
        eps = self._cfg.io_delta_epsilon
        return (
            stats["cpu_percent"] < self._cfg.idle_cpu_percent
            and abs(stats.get("net_io_delta", 0)) <= eps
            and abs(stats.get("block_io_delta", 0)) <= eps
        )

    def _is_emittable(self, track: _ContainerTrack) -> bool:
        return (
            track.consecutive >= self._n
            and self._clock.now() - track.idle_since >= self._cfg.idle_seconds
        )

    def _emit(
        self, name: str, stats: dict, track: _ContainerTrack
    ) -> ContainerCandidate:
        idle_s = self._clock.now() - track.idle_since
        cpu = stats["cpu_percent"]
        return ContainerCandidate(
            name=name,
            container_id=name,
            idle_seconds=idle_s,
            cpu_percent=cpu,
            reason=_reason(name, idle_s, cpu, self._n),
        )
