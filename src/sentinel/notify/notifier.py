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


def _default_os_runner(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=False)  # noqa: S602


class MacNotifier:
    """Fire one macOS notification per action via osascript.

    os_runner is injected so unit tests never touch the real binary.
    All failures are swallowed — notifications must never break execution.
    """

    def __init__(
        self,
        os_runner: Callable[[str], object] = _default_os_runner,
        config: NotifyConfig | None = None,
    ) -> None:
        self._runner = os_runner
        self._config = config or NotifyConfig()

    def notify(self, result: ActionResult) -> None:
        try:
            msg = f"{result.target} freed {format_bytes(result.bytes_freed)}"
            title = self._config.title
            cmd = f'osascript -e \'display notification "{msg}" with title "{title}"\''
            self._runner(cmd)
        except Exception:
            pass


class NullNotifier:
    """No-op notifier for dry-run mode or when NotifyConfig.enabled=False."""

    def notify(self, result: ActionResult) -> None:  # noqa: ARG002
        pass
