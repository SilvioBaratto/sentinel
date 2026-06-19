"""Public API for the threshold engine.

Re-exports the gate class and provides a stateless ``evaluate`` helper that
replays a sample sequence through a fresh ``DefaultThresholdEngine`` — useful
for tests and one-shot analyses where no persistent engine is maintained.
"""

from __future__ import annotations

from collections.abc import Sequence

from sentinel.config import MonitorConfig
from sentinel.domain.value_objects import CandidateSignal, ResourceSample
from sentinel.rules.hysteresis import HysteresisGate
from sentinel.rules.thresholds import DefaultThresholdEngine

__all__ = ["HysteresisGate", "evaluate"]


class _SingleSampleHistory:
    """Minimal History adaptor wrapping one ResourceSample."""

    def __init__(self, sample: ResourceSample) -> None:
        self._s = sample

    def latest(self) -> ResourceSample:
        return self._s

    def recent(self, n: int) -> tuple[ResourceSample, ...]:
        return (self._s,) if n >= 1 else ()

    def __len__(self) -> int:
        return 1

    def append(self, sample: ResourceSample) -> None:  # noqa: ARG002
        pass


def evaluate(
    samples: Sequence[ResourceSample],
    config: MonitorConfig | None = None,
) -> CandidateSignal:
    """Replay *samples* through a fresh engine and return the final CandidateSignal.

    Each sample is fed in order so the internal gates accumulate state exactly
    as they would in a live run — same input always produces the same output.
    """
    cfg = config or MonitorConfig()
    engine = DefaultThresholdEngine(cfg, lambda: 0.0)
    result: CandidateSignal | None = None
    for s in samples:
        result = engine.evaluate(_SingleSampleHistory(s))
    if result is None:
        raise ValueError("evaluate() requires at least one sample")
    return result
