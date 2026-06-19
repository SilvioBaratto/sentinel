"""ContainerAllowList — pure predicate for always-up vs idle-stop-eligible containers.

Driven by DockerConfig.always_up_prefixes / always_up_suffixes; no I/O.
is_always_up and is_eligible are mutually exclusive and exhaustive.
"""

from __future__ import annotations

from sentinel.config import DockerConfig


class ContainerAllowList:
    def __init__(self, config: DockerConfig = DockerConfig()) -> None:
        self._config = config

    def is_always_up(self, name: str) -> bool:
        return any(name.startswith(p) for p in self._config.always_up_prefixes) or any(
            name.endswith(s) for s in self._config.always_up_suffixes
        )

    def is_eligible(self, name: str) -> bool:
        return not self.is_always_up(name)
