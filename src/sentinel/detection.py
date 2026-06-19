"""
Composition root for the Cycle 2 detection pipeline.

``build_detection`` is the single place all real adapters (or injected fakes)
are wired together, mirroring ``build_pipeline`` from ``pipeline.py``.
``DetectionPipeline.detect(state)`` delegates to both idle detectors when
``state != NORMAL`` and returns an empty ``DetectionResult`` otherwise.

No action is ever taken here — this subsystem emits candidate lists only.
"""

from __future__ import annotations

from sentinel.config import MonitorConfig
from sentinel.domain.protocols import ContainerIdleDetector, ProcessIdleDetector
from sentinel.domain.value_objects import DetectionResult, SentinelState

_EMPTY = DetectionResult(processes=(), containers=())


class DetectionPipeline:
    """Thin orchestrator: one detect(state) call runs both idle detectors."""

    def __init__(
        self,
        process_detector: ProcessIdleDetector,
        container_detector: ContainerIdleDetector,
    ) -> None:
        self._proc = process_detector
        self._cont = container_detector

    def detect(self, state: SentinelState) -> DetectionResult:
        if state == SentinelState.NORMAL:
            return _EMPTY
        return DetectionResult(
            processes=tuple(self._proc.detect(state)),
            containers=tuple(self._cont.detect(state)),
        )


# ── real OS adapter bridge ────────────────────────────────────────────────────


class _DockerLiveReader:
    """Bridge DockerStatsReader + DockerSessionReader to the list_containers/get_stats interface.

    All docker imports are deferred to method bodies so constructing this class
    never touches the Docker daemon.  I/O deltas are computed from the previous
    snapshot so the idle detector receives the per-poll delta it expects.
    """

    def __init__(self) -> None:
        self._snapshot: dict = {}
        self._prev_io: dict = {}
        self._sessions: frozenset = frozenset()

    def list_containers(self) -> list[str]:
        from sentinel.docker.session_reader import DockerSessionReader  # deferred
        from sentinel.docker.stats_reader import DockerStatsReader  # deferred

        stats = DockerStatsReader().read()
        self._snapshot = {s.name: s for s in stats}
        try:
            self._sessions = DockerSessionReader().active_session_names()
        except Exception:
            self._sessions = frozenset()
        return list(self._snapshot.keys())

    def get_stats(self, name: str) -> dict:
        s = self._snapshot[name]
        net = s.net_rx_bytes + s.net_tx_bytes
        blk = s.block_read_bytes + s.block_write_bytes
        prev_net, prev_blk = self._prev_io.get(name, (net, blk))
        self._prev_io[name] = (net, blk)
        return {
            "cpu_percent": s.cpu_percent,
            "net_io_delta": net - prev_net,
            "block_io_delta": blk - prev_blk,
            "exec_active": name in self._sessions,
        }


# ── private wiring helpers ─────────────────────────────────────────────────────


def _os_detectors() -> tuple:
    """Deferred imports so a top-level import of detection never spawns a subprocess."""
    import time

    from sentinel.config import DockerConfig, ProcessConfig
    from sentinel.docker.idle_detector import DefaultContainerIdleDetector
    from sentinel.monitor.clock import SystemClock
    from sentinel.process.classifier import DefaultProcessClassifier
    from sentinel.process.frontmost import make_frontmost_reader
    from sentinel.process.hid import IoregHidIdleReader
    from sentinel.process.idle_detector import DefaultProcessIdleDetector
    from sentinel.process.lister import PsutilProcessLister

    proc_cfg = ProcessConfig()
    docker_cfg = DockerConfig()
    clock = SystemClock(monotonic=time.monotonic)

    proc_det = DefaultProcessIdleDetector(
        lister=PsutilProcessLister(config=proc_cfg),
        frontmost_reader=make_frontmost_reader(proc_cfg.use_nsworkspace_frontmost),
        hid_reader=IoregHidIdleReader(),
        classifier=DefaultProcessClassifier(proc_cfg),
        config=proc_cfg,
    )
    cont_det = DefaultContainerIdleDetector(
        reader=_DockerLiveReader(),
        clock=clock,
        config=docker_cfg,
    )
    return proc_det, cont_det


def build_detection(
    config: MonitorConfig,
    *,
    detectors: dict | None = None,
) -> DetectionPipeline:
    """Composition root: the only place real adapters (or injected fakes) are named together."""
    if detectors is not None:
        return DetectionPipeline(detectors["process"], detectors["container"])
    proc, cont = _os_detectors()
    return DetectionPipeline(proc, cont)
