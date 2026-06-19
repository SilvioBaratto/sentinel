from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from sentinel.domain.value_objects import SentinelState

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
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in fields})


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
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in mapping.items() if k in fields})
