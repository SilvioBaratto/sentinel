"""Plist generator for the Sentinel LaunchAgent — pure function, no I/O."""

from __future__ import annotations

import plistlib

from sentinel.config import ServiceConfig, SentinelPaths

_FIXED_KEYS: dict = {
    "RunAtLoad": True,
    "KeepAlive": {"Crashed": True, "SuccessfulExit": False},
    "ProcessType": "Background",
    "LowPriorityIO": True,
    "ThrottleInterval": 10,
}


def _variable_keys(
    config: ServiceConfig, paths: SentinelPaths, args: list[str]
) -> dict:
    log = paths.log_dir
    return {
        "Label": config.label,
        "ProgramArguments": list(args),
        "ExitTimeOut": int(config.exit_timeout),
        "StandardOutPath": str(log / "sentinel.out.log"),
        "StandardErrorPath": str(log / "sentinel.err.log"),
    }


def render_plist(
    config: ServiceConfig,
    paths: SentinelPaths,
    program_args: list[str],
) -> str:
    """Return an XML plist for the Sentinel LaunchAgent. Pure — no writes, no subprocess."""
    d = {**_FIXED_KEYS, **_variable_keys(config, paths, program_args)}
    return plistlib.dumps(d, fmt=plistlib.FMT_XML).decode()
