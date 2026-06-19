from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_GiB = 1024**3


@dataclass(frozen=True)
class MonitorConfig:
    """All sentinel thresholds and tunables in one place — nothing hard-coded downstream."""

    # Sampling
    interval: float = 30.0
    history_size: int = 120  # ~1 hour at 30 s intervals

    # Disk-low trigger
    disk_low_floor: int = 20 * _GiB

    # Hysteresis: how many consecutive samples must confirm a condition
    confirm_samples: int = 3  # elevate debounce
    confirm_samples_clear: int = 5  # return-to-normal debounce (asymmetric)

    # Cooldown between de-escalation flips (seconds)
    cooldown: float = 300.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> MonitorConfig:
        """Construct from a plain dict (e.g. parsed TOML/JSON); no file I/O."""
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in fields})
