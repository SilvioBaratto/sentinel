"""MacNotifier and NullNotifier — fail-safe per-action macOS notifications.

Implements Notifier: notify(ActionResult) -> None.
Both sinks catch everything internally — a notification failure must never
propagate into the execution engine.
"""

from __future__ import annotations

import subprocess
import time
from typing import Callable

from sentinel.config import NotifyConfig
from sentinel.domain.value_objects import ActionResult
from sentinel.fmt import format_action_message

# Suppress an identical (kind, target, success) banner for this long. The daemon
# ticks every 30s against the same targets, so without coalescing a truthful
# failure message is ~1,600 identical banners a day.
_REPEAT_SUPPRESS_SECONDS = 3600.0


def _default_os_runner(args: list[str]) -> None:
    # argv list, NO shell: result.target may be an attacker-influenced file path.
    subprocess.run(args, check=False)


def _escape_applescript(text: str) -> str:
    """Escape a value for safe embedding in an AppleScript double-quoted literal."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


class MacNotifier:
    """Fire one macOS notification per action via osascript.

    os_runner is injected so unit tests never touch the real binary.
    All failures are swallowed — notifications must never break execution.
    """

    def __init__(
        self,
        os_runner: Callable[[list[str]], object] = _default_os_runner,
        config: NotifyConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = os_runner
        self._config = config or NotifyConfig()
        self._monotonic = monotonic
        self._last_seen: dict[tuple[str, str, bool], float] = {}

    def notify(self, result: ActionResult) -> None:
        try:
            if self._suppressed(result):
                return
            msg = _escape_applescript(format_action_message(result))
            title = _escape_applescript(self._config.title)
            script = f'display notification "{msg}" with title "{title}"'
            self._runner(["osascript", "-e", script])
        except Exception:
            pass

    def _suppressed(self, result: ActionResult) -> bool:
        """True when this exact outcome was already announced recently."""
        key = (
            str(getattr(result.kind, "value", result.kind)),
            str(result.target),
            bool(result.success),
        )
        now = self._monotonic()
        last = self._last_seen.get(key)
        if last is not None and (now - last) < _REPEAT_SUPPRESS_SECONDS:
            return True
        self._last_seen[key] = now
        self._evict(now)
        return False

    def _evict(self, now: float) -> None:
        """Drop expired keys so a long-lived daemon cannot grow this unbounded."""
        if len(self._last_seen) <= 256:
            return
        cutoff = now - _REPEAT_SUPPRESS_SECONDS
        self._last_seen = {k: t for k, t in self._last_seen.items() if t >= cutoff}


class NullNotifier:
    """No-op notifier for dry-run mode or when NotifyConfig.enabled=False."""

    def notify(self, result: ActionResult) -> None:  # noqa: ARG002
        pass
