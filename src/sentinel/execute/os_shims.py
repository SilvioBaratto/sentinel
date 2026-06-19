"""Public re-export surface for the OS kill shims.

Import from here to avoid coupling to internal module layout.
"""

from __future__ import annotations

from sentinel.execute.quitter import OsascriptAppQuitter
from sentinel.execute.signals import PosixAliveProbe, PosixProcessSignaler

__all__ = ["OsascriptAppQuitter", "PosixAliveProbe", "PosixProcessSignaler"]
