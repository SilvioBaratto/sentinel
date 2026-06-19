"""Sentinel service package — daemon loop, plist, launchctl controller, status."""

from sentinel.service.daemon import SentinelDaemon, build_daemon
from sentinel.service.status_provider import DefaultStatusProvider

__all__ = ["SentinelDaemon", "build_daemon", "DefaultStatusProvider"]
