"""DockerPortDiscoverer — discovers published ports and compose project for a container.

Parses `docker inspect` JSON. Implements PortDiscoverer.
Fail-safe: any exception → StackPorts(stack=name, containers=(name,), ports=()).
Port source precedence:
  1. NetworkSettings.Ports  (populated when running)
  2. HostConfig.PortBindings (persisted in both running and stopped states)
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable

from sentinel.domain.value_objects import PublishedPort, StackPorts


def _default_docker(args: list[str]) -> str:
    return subprocess.check_output(["docker"] + args, text=True)


def _safe_default(name: str) -> StackPorts:
    return StackPorts(stack=name, containers=(name,), ports=())


def _parse_one_binding(port_proto: str, bind: dict) -> PublishedPort | None:
    host_port = bind.get("HostPort", "")
    if not host_port:
        return None
    cport, _, proto = port_proto.partition("/")
    return PublishedPort(
        bind.get("HostIp", "0.0.0.0"), int(host_port), int(cport), proto or "tcp"
    )


def _parse_port_bindings(bindings: dict) -> tuple[PublishedPort, ...]:
    result = []
    for port_proto, binds in bindings.items():
        for bind in binds or []:
            port = _parse_one_binding(port_proto, bind)
            if port is not None:
                result.append(port)
    return tuple(result)


class DockerPortDiscoverer:
    def __init__(self, docker: Callable[[list[str]], str] = _default_docker) -> None:
        self._docker = docker

    def discover(self, name: str) -> StackPorts:
        try:
            raw = self._docker(["inspect", name])
            containers = json.loads(raw)
            if not containers:
                return _safe_default(name)
            return self._build(name, containers[0])
        except Exception:
            return _safe_default(name)

    def _build(self, name: str, container: dict) -> StackPorts:
        return StackPorts(
            stack=name,
            containers=(name,),
            ports=self._extract_ports(container),
            compose_project=self._extract_compose_project(container),
        )

    def _extract_ports(self, container: dict) -> tuple[PublishedPort, ...]:
        network = (container.get("NetworkSettings") or {}).get("Ports") or {}
        if network:
            parsed = _parse_port_bindings(network)
            if parsed:
                return parsed
        host_cfg = (container.get("HostConfig") or {}).get("PortBindings") or {}
        return _parse_port_bindings(host_cfg)

    def _extract_compose_project(self, container: dict) -> str | None:
        labels = (container.get("Config") or {}).get("Labels") or {}
        return labels.get("com.docker.compose.project")
