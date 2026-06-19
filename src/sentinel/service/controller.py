"""launchctl service lifecycle controller for the Sentinel LaunchAgent."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from sentinel.service.plist import render_plist


def _default_runner(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _parse_state(output: str) -> str:
    """Extract the service state line from 'launchctl print' output."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("state"):
            _, _, value = stripped.partition("=")
            return value.strip() or "unknown"
    return output.strip() or "unknown"


def _program_args(config) -> list[str]:
    exe = getattr(config, "python_executable", None) or sys.executable
    return [exe, "-m", "sentinel.cli", "run"]


def _resolve_la_dir(override: Path | None, paths) -> Path:
    if override is not None:
        return Path(override)
    if la := getattr(paths, "launch_agents_dir", None):
        return Path(la)
    return Path.home() / "Library" / "LaunchAgents"


def _run_safe(runner: Callable, cmd: list[str]) -> None:
    try:
        runner(cmd)
    except subprocess.CalledProcessError:
        pass


def _bootstrap_or_load(runner: Callable, domain: str, plist: str) -> None:
    try:
        runner(["launchctl", "bootstrap", domain, plist])
    except subprocess.CalledProcessError:
        _run_safe(runner, ["launchctl", "load", plist])


def _bootout_or_unload(runner: Callable, target: str, plist: str) -> None:
    try:
        runner(["launchctl", "bootout", target])
    except subprocess.CalledProcessError:
        _run_safe(runner, ["launchctl", "unload", plist])


class LaunchctlServiceController:
    """Manages the Sentinel LaunchAgent via launchctl in the gui/$UID domain."""

    def __init__(
        self,
        config,
        paths,
        launch_agents_dir: Path | None = None,
        runner: Callable | None = None,
    ) -> None:
        self._config = config
        self._paths = paths
        self._la_dir = _resolve_la_dir(launch_agents_dir, paths)
        self._runner = runner or _default_runner
        self._uid = os.getuid()

    def install(self) -> None:
        plist_xml = render_plist(self._config, self._paths, _program_args(self._config))
        plist_path = self._plist_path()
        plist_path.write_text(plist_xml, encoding="utf-8")
        _bootstrap_or_load(self._runner, f"gui/{self._uid}", str(plist_path))

    def uninstall(self) -> None:
        _bootout_or_unload(self._runner, self._target(), str(self._plist_path()))
        try:
            self._plist_path().unlink()
        except FileNotFoundError:
            pass

    def start(self) -> None:
        _run_safe(self._runner, ["launchctl", "kickstart", self._target()])

    def stop(self) -> None:
        _run_safe(self._runner, ["launchctl", "kill", "SIGTERM", self._target()])

    def status(self) -> str:
        try:
            result = self._runner(["launchctl", "print", self._target()])
            return _parse_state(result.stdout or "")
        except subprocess.CalledProcessError as exc:
            return f"error: {exc.stderr or 'launchd unavailable'}"

    def _target(self) -> str:
        return f"gui/{self._uid}/{self._config.label}"

    def _plist_path(self) -> Path:
        return self._la_dir / f"{self._config.label}.plist"
