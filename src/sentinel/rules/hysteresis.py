from __future__ import annotations

from typing import Callable


class HysteresisGate:
    """Debounce: require N consecutive True conditions before confirming; enforce cooldown.

    Cooldown is directional by design — callers inject cooldown_s=0 for escalation
    gates and config.cooldown for de-escalation gates, so the engine is fast to
    escalate and slow to clear.
    """

    def __init__(
        self,
        confirm_samples: int,
        cooldown_s: float,
        clock: Callable[[], float],
    ) -> None:
        self._confirm = confirm_samples
        self._cooldown = cooldown_s
        self._clock = clock
        self._streak = 0
        self._last_flip: float | None = None

    def confirmed(self, condition: bool, now: float) -> bool:
        if not condition:
            self._streak = 0
            return False
        self._streak += 1
        if self._streak < self._confirm or self._in_cooldown(now):
            return False
        self._streak, self._last_flip = 0, now
        return True

    def _in_cooldown(self, now: float) -> bool:
        return self._last_flip is not None and (now - self._last_flip) < self._cooldown
