"""ProjectActivityGuard — activity gate for build-artifact permanent deletion.

Fail-safe direction: any uncertainty → True (active, skip delete).
The guard is intentionally conservative: when in doubt, protect.
"""

from __future__ import annotations

import os
import pathlib
import time
from typing import Callable

from sentinel.config import CleanupConfig


def _list_running_containers() -> list:
    import docker  # lazy: only needed when not injected in tests

    return docker.from_env().containers.list()


class ProjectActivityGuard:
    """Returns True when a project is active and must NOT be permanently deleted.

    Active means: a running container mounts into project_dir, OR a git marker
    (.git, .git/index, .git/HEAD) was modified within git_recent_seconds.

    Any probe error → True (fail-safe: skip delete rather than risk data loss).
    """

    def __init__(
        self,
        running_containers: Callable[[], list] | None = None,
        clock: Callable[[], float] | None = None,
        config: CleanupConfig | None = None,
        mtime: Callable[[str], float] = os.path.getmtime,
    ) -> None:
        self._containers = running_containers or _list_running_containers
        self._clock = clock or time.time
        self._config = config or CleanupConfig()
        self._mtime = mtime

    def is_active(self, project_dir: str | pathlib.Path) -> bool:
        try:
            return self._has_running_container(project_dir) or self._git_recent(
                project_dir
            )
        except Exception:
            return True

    # ── Docker probe ──────────────────────────────────────────────────────────

    def _has_running_container(self, project_dir: str | pathlib.Path) -> bool:
        try:
            project = pathlib.Path(project_dir).resolve()
            return any(self._mount_covers(c, project) for c in self._containers())
        except Exception:
            return True

    def _mount_covers(self, container, project: pathlib.Path) -> bool:
        try:
            for mount in container.attrs.get("Mounts", []):
                source = mount.get("Source", "")
                if source and self._paths_overlap(pathlib.Path(source), project):
                    return True
            return False
        except Exception:
            return True

    def _paths_overlap(self, source: pathlib.Path, project: pathlib.Path) -> bool:
        src = source.resolve()
        return (
            src == project or project.is_relative_to(src) or src.is_relative_to(project)
        )

    # ── Git probe ─────────────────────────────────────────────────────────────

    def _git_recent(self, project_dir: str | pathlib.Path) -> bool:
        try:
            return self._check_git(project_dir)
        except Exception:
            return True

    def _check_git(self, project_dir: str | pathlib.Path) -> bool:
        git = pathlib.Path(project_dir) / ".git"
        now = self._clock()
        threshold = self._config.git_recent_seconds
        candidates = [git, git / "index", git / "HEAD"]
        return any(self._is_recently_modified(p, now, threshold) for p in candidates)

    def _is_recently_modified(
        self, path: pathlib.Path, now: float, threshold: float
    ) -> bool:
        try:
            age = now - self._mtime(str(path))
            return age < threshold
        except FileNotFoundError:
            return False
