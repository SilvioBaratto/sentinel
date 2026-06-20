"""MacNotifier and NullNotifier — fail-safe per-action macOS notifications.

Implements Notifier: notify(ActionResult) -> None.
Both sinks catch everything internally — a notification failure must never
propagate into the execution engine.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from sentinel.config import NotifyConfig
from sentinel.domain.value_objects import ActionResult
from sentinel.fmt import format_bytes


def _default_os_runner(args: list[str]) -> None:
    # argv list, NO shell: result.target may be an attacker-influenced file path.
    subprocess.run(args, check=False)


def _escape_applescript(text: str) -> str:
    """Escape a value for safe embedding in an AppleScript double-quoted literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace(
        "\r", " "
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
    ) -> None:
        self._runner = os_runner
        self._config = config or NotifyConfig()

    def notify(self, result: ActionResult) -> None:
        try:
            msg = _escape_applescript(
                f"{result.target} freed {format_bytes(result.bytes_freed)}"
            )
            title = _escape_applescript(self._config.title)
            script = f'display notification "{msg}" with title "{title}"'
            self._runner(["osascript", "-e", script])
        except Exception:
            pass


class NullNotifier:
    """No-op notifier for dry-run mode or when NotifyConfig.enabled=False."""

    def notify(self, result: ActionResult) -> None:  # noqa: ARG002
        pass
