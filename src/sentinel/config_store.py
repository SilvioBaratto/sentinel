"""JSON-backed config store and path resolver for Sentinel.

Atomic writes: temp file lives beside config.json (same filesystem),
so os.replace() is a true atomic rename on POSIX.
"""

from __future__ import annotations

import json
import os
import pathlib

from sentinel.config import AppConfig, SentinelPaths


class JsonConfigStore:
    """Persists AppConfig to disk as JSON; fail-safe on missing or corrupt files."""

    def __init__(self, base_dir: str | pathlib.Path | None = None) -> None:
        if base_dir is None:
            self._base = pathlib.Path(
                os.path.expanduser("~/Library/Application Support/Sentinel")
            )
        else:
            self._base = pathlib.Path(base_dir)

    def load(self) -> AppConfig:
        self._base.mkdir(parents=True, exist_ok=True)
        try:
            raw = (self._base / "config.json").read_bytes()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return AppConfig()
            return AppConfig.from_mapping(data)
        except Exception:
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        tmp = self._base / "config.json.tmp"
        tmp.write_text(json.dumps(config.to_mapping(), indent=2), encoding="utf-8")
        os.replace(tmp, self._base / "config.json")

    def paths(self) -> SentinelPaths:
        return SentinelPaths(
            config_path=str(self._base / "config.json"),
            audit_log_path=str(self._base / "sentinel.audit.jsonl"),
            state_path=str(self._base / "state.json"),
            launch_agents_dir=os.path.expanduser("~/Library/LaunchAgents"),
        )


def resolve_paths() -> SentinelPaths:
    """Return the default Sentinel runtime paths without touching the filesystem."""
    return SentinelPaths.default()
