from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from sentinel.domain.value_objects import ExecutionMode, SentinelState


def _to_json(value: Any) -> Any:
    """Recursively convert a dataclass to a JSON-safe dict (tuples→lists, enums→values)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_json(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_to_json(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _coerce_tuples(mapping: dict[str, Any], *names: str) -> dict[str, Any]:
    """Return a copy of *mapping* with named list fields coerced to tuples."""
    result = dict(mapping)
    for name in names:
        if isinstance(result.get(name), list):
            result[name] = tuple(result[name])
    return result


# Alias so both names work in tests and downstream code
SystemState = SentinelState

_GiB = 1024**3

# Never-kill list: terminals (incl. claude), VPN, password managers, Docker infra, backup agents.
# This list is the project default — never hard-code these names downstream.
_DEFAULT_PROTECTED_NAMES: tuple[str, ...] = (
    "Terminal",
    "iTerm2",
    "iTerm",
    "claude",
    "ssh",
    "tmux",
    "screen",
    "login",
    # VPN clients
    "Mullvad VPN",
    "Tunnelblick",
    "OpenVPN",
    "Viscosity",
    "Cisco Secure Client",
    "GlobalProtect",
    # Tailscale — GUI app, CLI, open-source daemon, and the macsys network
    # system-extension. Must never be reaped: it is the box's remote lifeline.
    "Tailscale",
    "tailscale",
    "tailscaled",
    "io.tailscale.ipn.macsys.network-extension",
    # Password managers
    "1Password",
    "1Password 7",
    "Bitwarden",
    "KeePassXC",
    "LastPass",
    "Dashlane",
    # Docker infra
    "Docker Desktop",
    "colima",
    # Backup agents
    "Backblaze",
    "Time Machine",
    "Arq",
    "Arq Agent",
    "Carbon Copy Cloner",
    "SuperDuper!",
)

# Reap allow-list: GUI apps eligible for idle reaping.
# Never-kill names always win over this list.
_DEFAULT_REAP_ALLOW_LIST: tuple[str, ...] = (
    # Browsers
    "Google Chrome",
    "Chrome",
    "Safari",
    "Firefox",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
    # Code editors
    "Code",
    "Visual Studio Code",
    "Cursor",
    "IntelliJ IDEA",
    "PyCharm",
    "WebStorm",
    "GoLand",
    "CLion",
    "Rider",
    "DataGrip",
    "RubyMine",
    "PhpStorm",
    # Electron chat apps
    "Slack",
    "Discord",
    "Microsoft Teams",
    "WhatsApp",
    "Telegram",
    "Signal",
    "Zoom",
)


@dataclass(frozen=True)
class MonitorConfig:
    """All sentinel thresholds and tunables in one place — nothing hard-coded downstream."""

    # Sampling
    interval: float = 30.0
    history_size: int = 120  # ~1 hour at 30 s intervals

    # Disk-low trigger
    disk_low_floor: int = 20 * _GiB

    # Hysteresis: how many consecutive samples must confirm a condition
    confirm_samples: int = 3  # elevate debounce
    confirm_samples_clear: int = 5  # return-to-normal debounce (asymmetric)

    # Cooldown between de-escalation flips (seconds)
    cooldown: float = 300.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> MonitorConfig:
        """Construct from a plain dict (e.g. parsed TOML/JSON); no file I/O."""
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in fields})


@dataclass(frozen=True)
class ProcessConfig:
    """Per-process idle detection and classification config. One concern, SRP."""

    idle_cpu_percent: float = 1.0
    idle_seconds: float = 7200.0
    cpu_sample_interval: float = 1.0
    heavy_rss_floor: int = 0
    use_nsworkspace_frontmost: bool = False
    protected_names: tuple[str, ...] = _DEFAULT_PROTECTED_NAMES
    shell_session_markers: tuple[str, ...] = (
        "login",
        "-zsh",
        "-bash",
        "zsh",
        "bash",
        "Terminal",
        "iTerm2",
        "tmux",
        "screen",
        "ssh",
    )
    reap_allow_list: tuple[str, ...] = _DEFAULT_REAP_ALLOW_LIST

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> ProcessConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = _coerce_tuples(
            {k: v for k, v in mapping.items() if k in known},
            "protected_names",
            "shell_session_markers",
            "reap_allow_list",
        )
        return cls(**filtered)


@dataclass(frozen=True)
class DockerConfig:
    """Docker idle detection config. One concern, SRP."""

    idle_cpu_percent: float = 0.5
    idle_seconds: float = 7200.0
    consecutive_polls: int = 3
    io_delta_epsilon: int = 0
    always_up_prefixes: tuple[str, ...] = ("optimizer_",)
    always_up_suffixes: tuple[str, ...] = ("_db",)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> DockerConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = _coerce_tuples(
            {k: v for k, v in mapping.items() if k in known},
            "always_up_prefixes",
            "always_up_suffixes",
        )
        return cls(**filtered)


# ── Cycle 3: safe execution & disk cleanup config ────────────────────────────

_DEFAULT_EDITOR_NAMES: tuple[str, ...] = (
    "Code",
    "Visual Studio Code",
    "Cursor",
    "IntelliJ IDEA",
    "PyCharm",
    "WebStorm",
    "GoLand",
    "CLion",
    "Rider",
    "DataGrip",
    "RubyMine",
    "PhpStorm",
)

_DEFAULT_DENY_PATHS: tuple[str, ...] = (
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/Library/Application Support",
    os.path.expanduser("~/Library/Application Support"),
    "/Applications",
)


@dataclass(frozen=True)
class KillConfig:
    quit_grace_seconds: float = 45.0
    editor_quit_grace_seconds: float = 60.0
    sigterm_grace_seconds: float = 20.0
    poll_interval: float = 1.0
    critical_quit_grace_seconds: float = 30.0
    editor_names: tuple[str, ...] = _DEFAULT_EDITOR_NAMES
    editor_auto_sigkill: bool = False

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> KillConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = _coerce_tuples(
            {k: v for k, v in mapping.items() if k in known},
            "editor_names",
        )
        return cls(**filtered)


# Alias used by VerifiedKiller and tests — same config, friendlier name.
SentinelConfig = KillConfig


@dataclass(frozen=True)
class CleanupConfig:
    disk_low_floor: int = 20 * _GiB
    downloads_max_age_days: int = 30
    build_artifact_names: tuple[str, ...] = (
        "node_modules",
        ".next",
        "dist",
        "__pycache__",
        "DerivedData",
    )
    deny_paths: tuple[str, ...] = _DEFAULT_DENY_PATHS
    cache_globs: tuple[str, ...] = ()
    downloads_dir: str | None = None
    git_recent_seconds: float = 3600.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> CleanupConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = _coerce_tuples(
            {k: v for k, v in mapping.items() if k in known},
            "build_artifact_names",
            "deny_paths",
            "cache_globs",
        )
        return cls(**filtered)


@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool = True
    title: str = "Sentinel"

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> NotifyConfig:
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in fields})


@dataclass(frozen=True)
class ExecuteConfig:
    mode: ExecutionMode = ExecutionMode.AUTO
    audit_log_path: str | None = None
    rotate_max_bytes: int | None = None
    rotate_backups: int | None = None
    kill: KillConfig = field(default_factory=KillConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> ExecuteConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered: dict[str, Any] = {k: v for k, v in mapping.items() if k in known}
        if isinstance(filtered.get("mode"), str):
            filtered["mode"] = ExecutionMode(filtered["mode"])
        if isinstance(filtered.get("kill"), dict):
            filtered["kill"] = KillConfig.from_mapping(filtered["kill"])
        if isinstance(filtered.get("cleanup"), dict):
            filtered["cleanup"] = CleanupConfig.from_mapping(filtered["cleanup"])
        if isinstance(filtered.get("notify"), dict):
            filtered["notify"] = NotifyConfig.from_mapping(filtered["notify"])
        return cls(**filtered)


# ── Cycle 4: wake proxy, service, advisor, paths & aggregate config ─────────


@dataclass(frozen=True)
class WakeProxyConfig:
    enabled: bool = True
    health_timeout: float = 30.0
    health_poll_interval: float = 0.25
    listen_backlog: int = 128
    connect_buffer: int = 65536
    bind_host: str = "127.0.0.1"
    first_hit_hold: float = 30.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> WakeProxyConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in known})


@dataclass(frozen=True)
class ServiceConfig:
    label: str = "com.silviobaratto.sentinel"
    run_at_load: bool = True
    keep_alive_crashed: bool = True
    keep_alive_successful_exit: bool = False
    process_type: str = "Background"
    low_priority_io: bool = True
    throttle_interval: int = 10
    exit_timeout: float = 20.0
    min_lifetime: float = 60.0
    interval: float = 30.0
    python_executable: str | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> ServiceConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in known})


@dataclass(frozen=True)
class AdvisorConfig:
    enabled: bool = False
    base_url: str = "http://localhost:11434"
    model: str = "glm-5.2:cloud"
    keep_alive: int = 0
    request_timeout: float = 5.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> AdvisorConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in known})


@dataclass(frozen=True)
class SentinelPaths:
    config_path: str
    audit_log_path: str
    state_path: str
    launch_agents_dir: str

    @property
    def log_dir(self) -> Path:
        return Path(self.audit_log_path).parent

    @classmethod
    def default(cls) -> SentinelPaths:
        base = os.path.expanduser("~/Library/Application Support/Sentinel")
        return cls(
            config_path=os.path.join(base, "config.json"),
            audit_log_path=os.path.join(base, "sentinel.audit.jsonl"),
            state_path=os.path.join(base, "state.json"),
            launch_agents_dir=os.path.expanduser("~/Library/LaunchAgents"),
        )


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    process: ProcessConfig = field(default_factory=ProcessConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    execute: ExecuteConfig = field(default_factory=ExecuteConfig)
    wake: WakeProxyConfig = field(default_factory=WakeProxyConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    advisor: AdvisorConfig = field(default_factory=AdvisorConfig)

    def to_mapping(self) -> dict[str, Any]:
        """Produce a JSON-safe dict (tuples→lists, enums→values)."""
        return {
            "monitor": _to_json(self.monitor),
            "process": _to_json(self.process),
            "docker": _to_json(self.docker),
            "execute": _to_json(self.execute),
            "wake": _to_json(self.wake),
            "service": _to_json(self.service),
            "advisor": _to_json(self.advisor),
        }

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> AppConfig:
        """Reconstruct from a plain dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered: dict[str, Any] = {k: v for k, v in mapping.items() if k in known}
        _sub_from(filtered, "monitor", MonitorConfig)
        _sub_from(filtered, "process", ProcessConfig)
        _sub_from(filtered, "docker", DockerConfig)
        _sub_from(filtered, "execute", ExecuteConfig)
        _sub_from(filtered, "wake", WakeProxyConfig)
        _sub_from(filtered, "service", ServiceConfig)
        _sub_from(filtered, "advisor", AdvisorConfig)
        return cls(**filtered)

    @property
    def effective_audit_log_path(self) -> str:
        """Explicit execute.audit_log_path wins; falls back to SentinelPaths default."""
        return self.execute.audit_log_path or SentinelPaths.default().audit_log_path


def _sub_from(filtered: dict[str, Any], key: str, sub_cls: type) -> None:
    """Deserialise a nested sub-config dict in-place, no-op if already the right type."""
    if isinstance(filtered.get(key), dict):
        filtered[key] = sub_cls.from_mapping(filtered[key])


# Re-export so callers can use either `sentinel.config` or `sentinel.config_store`.
# This is LAZY (PEP 562 module __getattr__): an eager top-level import here creates a
# circular import — config_store does `from sentinel.config import AppConfig, SentinelPaths`,
# so whichever module is imported first would hit the other half-initialised. Deferring the
# import to attribute-access time breaks the cycle while keeping `sentinel.config.JsonConfigStore`
# and `sentinel.config.resolve_paths` working for callers that expect them here.
_CONFIG_STORE_EXPORTS = frozenset({"JsonConfigStore", "resolve_paths"})


def __getattr__(name: str) -> Any:
    if name in _CONFIG_STORE_EXPORTS:
        import sentinel.config_store as _cs  # noqa: PLC0415

        return getattr(_cs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
