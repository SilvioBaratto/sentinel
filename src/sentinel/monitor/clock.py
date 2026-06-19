from __future__ import annotations

import time
from typing import Callable

__all__ = ["SystemClock"]


class SystemClock:
    """Monotonic clock — immune to wall-clock jumps and NTP steps.

    Inject a fake callable in tests; production default is time.monotonic.
    """

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic

    def now(self) -> float:
        return self._monotonic()
