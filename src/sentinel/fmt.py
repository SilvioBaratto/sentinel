"""Pure SI decimal byte formatter shared by the audit logger and notifier."""

from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")
_STEP = 1_000


def format_bytes(n: int) -> str:
    """Return a human-readable SI decimal string, e.g. 1_200_000_000 → '1.2 GB'."""
    if n < _STEP:
        return f"{n} B"
    value = float(n)
    unit = "B"
    for unit in _UNITS[1:]:
        value /= _STEP
        if value < _STEP or unit == _UNITS[-1]:
            break
    return f"{value:.1f} {unit}"
