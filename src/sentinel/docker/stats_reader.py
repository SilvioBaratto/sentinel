"""DockerStatsReader — parses docker container stats --no-stream output.

Injects its `docker` callable so tests run without a real daemon.
Fail-safe: any exception during read/parse → empty tuple, never raises.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable

from sentinel.docker.byte_parser import parse_byte_pair
from sentinel.domain.value_objects import ContainerStats


def _default_docker(args: list[str]) -> str:
    return subprocess.check_output(["docker"] + args, text=True)


class DockerStatsReader:
    def __init__(self, docker: Callable[[list[str]], str] = _default_docker) -> None:
        self._docker = docker

    def read(self) -> tuple[ContainerStats, ...]:
        try:
            raw = self._docker(
                ["container", "stats", "--no-stream", "--format", "{{json .}}"]
            )
            return self._parse(raw)
        except Exception:
            return ()

    def _parse(self, raw: str) -> tuple[ContainerStats, ...]:
        lines = (ln.strip() for ln in raw.splitlines() if ln.strip())
        parsed = (self._parse_line(ln) for ln in lines)
        return tuple(p for p in parsed if p is not None)

    def _parse_line(self, line: str) -> ContainerStats | None:
        try:
            return self._build_stats(json.loads(line))
        except Exception:
            return None

    def _build_stats(self, data: dict) -> ContainerStats:
        net_rx, net_tx = parse_byte_pair(data["NetIO"])
        block_r, block_w = parse_byte_pair(data["BlockIO"])
        return ContainerStats(
            container_id=data["ID"],
            name=data["Name"],
            cpu_percent=float(data["CPUPerc"].rstrip("%")),
            net_rx_bytes=net_rx,
            net_tx_bytes=net_tx,
            block_read_bytes=block_r,
            block_write_bytes=block_w,
        )
