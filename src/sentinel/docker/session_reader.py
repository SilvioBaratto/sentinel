"""DockerSessionReader — surfaces containers with a live exec/attach session.

In-use sticky signal: a container with active ExecIDs is never a stop candidate.
Detection: docker ps -q → collect running IDs, then docker container inspect → parse ExecIDs.
Fail-safe: any exception → empty frozenset, never raises into the detector.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable


def _default_docker(args: list[str]) -> str:
    return subprocess.check_output(["docker"] + args, text=True)


class DockerSessionReader:
    def __init__(self, docker: Callable[[list[str]], str] = _default_docker) -> None:
        self._docker = docker

    def active_session_names(self) -> frozenset[str]:
        try:
            return self._read_sessions()
        except Exception:
            return frozenset()

    def _read_sessions(self) -> frozenset[str]:
        ids = self._get_running_ids()
        if not ids:
            return frozenset()
        raw = self._docker(["container", "inspect"] + ids)
        return self._parse_inspect(raw)

    def _get_running_ids(self) -> list[str]:
        raw = self._docker(["ps", "-q", "--no-trunc"])
        return [ln for ln in raw.strip().splitlines() if ln.strip()]

    def _parse_inspect(self, raw: str) -> frozenset[str]:
        containers = json.loads(raw)
        return frozenset(
            entry["Name"].lstrip("/")
            for entry in containers
            if (entry.get("ExecIDs") or []) and entry.get("Name")
        )
