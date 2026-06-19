"""DockerContainerStopper — reversible docker stop, allow-list guarded.

Implements ContainerStopper: stop(candidate) -> ActionResult.
Never invokes docker with rm, --volumes, -v, volume, or prune.
Always-up containers are refused with zero docker calls.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from sentinel.config import DockerConfig
from sentinel.docker.allow_list import ContainerAllowList
from sentinel.domain.value_objects import ActionKind, ActionResult, Reversibility


def _default_docker(args: list[str]) -> str:
    return subprocess.check_output(["docker"] + args, text=True)


class DockerContainerStopper:
    """Stop an idle container via `docker stop <name>`, guarded by allow-list.

    Always-up containers (optimizer_* / *_db) return success=False with zero
    docker calls. All other errors are caught — stop() never raises.
    """

    def __init__(
        self,
        docker: Callable[[list[str]], str] = _default_docker,
        allow_list: ContainerAllowList | None = None,
        config: DockerConfig | None = None,
    ) -> None:
        self._docker = docker
        cfg = config or DockerConfig()
        self._allow_list = allow_list or ContainerAllowList(cfg)

    def stop(self, candidate) -> ActionResult:
        name = candidate.name
        if self._allow_list.is_always_up(name):
            return self._refused(name)
        return self._do_stop(name)

    def _refused(self, name: str) -> ActionResult:
        return ActionResult(
            kind=ActionKind.STOP_CONTAINER,
            target=name,
            success=False,
            reversibility=Reversibility.REVERSIBLE,
            detail=f"always-up container refused: {name}",
        )

    def _do_stop(self, name: str) -> ActionResult:
        try:
            self._docker(["stop", name])
            return ActionResult(
                kind=ActionKind.STOP_CONTAINER,
                target=name,
                success=True,
                reversibility=Reversibility.REVERSIBLE,
            )
        except Exception as exc:
            return ActionResult(
                kind=ActionKind.STOP_CONTAINER,
                target=name,
                success=False,
                reversibility=Reversibility.REVERSIBLE,
                detail=str(exc),
            )
