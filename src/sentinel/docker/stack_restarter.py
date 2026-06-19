"""DockerStackRestarter — restarts a stopped stack via WakeRegistration.restart_command.

Implements StackRestarter. Safety guard rejects forbidden destructive tokens using
exact-equality matching (never substring). Fail-safe: any exception → RESTART_FAILED.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from sentinel.domain.value_objects import WakeOutcome, WakeRegistration

_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {"rm", "--volumes", "-v", "volume", "prune"}
)


def _assert_safe_docker_args(args: list[str]) -> None:
    """Raise AssertionError if any element exactly matches a forbidden token."""
    for token in args:
        if token in _FORBIDDEN_TOKENS:
            raise AssertionError(f"Forbidden docker token {token!r} in args: {args}")


def _default_docker(args: list[str]) -> str:
    return subprocess.check_output(["docker"] + args, text=True)


class DockerStackRestarter:
    def __init__(self, docker: Callable[[list[str]], str] = _default_docker) -> None:
        self._docker = docker

    def is_running(self, stack: str) -> bool:
        try:
            output = self._docker(["ps", "--format", "{{.Names}}"])
            return stack in output
        except Exception:
            return False

    def restart(self, registration: WakeRegistration) -> WakeOutcome:
        if self.is_running(registration.stack):
            return WakeOutcome.ALREADY_RUNNING
        return self._do_restart(registration)

    def _do_restart(self, registration: WakeRegistration) -> WakeOutcome:
        try:
            cmd = list(registration.restart_command)
            _assert_safe_docker_args(cmd)
            self._docker(cmd)
            return WakeOutcome.RESTARTED
        except Exception:
            return WakeOutcome.RESTART_FAILED
