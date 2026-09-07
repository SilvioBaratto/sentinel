"""Pure formatters shared by the audit logger, the notifier and the CLI."""

from __future__ import annotations

from typing import Any

_UNITS = ("B", "KB", "MB", "GB", "TB")
_STEP = 1_000


_VERBS = {
    "trash": ("moved to Trash", "could not move to Trash"),
    "delete": ("deleted", "could not delete"),
    "stop_container": ("stopped", "could not stop"),
    "kill_process": ("quit", "could not quit"),
}


def format_action_message(result: Any) -> str:
    """Describe one ActionResult truthfully, for a notification.

    The previous notifier rendered every result as ``"<target> freed <bytes>"``
    without reading ``success``, so a hard failure and a completed reclaim were
    indistinguishable — a 100%-failing cleanup was announced as a success ~1,600
    times a day.  Two rules here: a failure never reads as a success, and a
    trash is never called "freed" (it relocates bytes within the volume).
    """
    target = str(getattr(result, "target", "") or "?")
    ok = bool(getattr(result, "success", False))
    kind = str(getattr(getattr(result, "kind", None), "value", "") or "action")
    done, failed = _VERBS.get(kind, (f"{kind} ok", f"{kind} failed"))
    if not ok:
        detail = str(getattr(result, "detail", "") or "")
        suffix = f": {detail}" if detail else ""
        return f"FAILED — {failed} {target}{suffix}"
    freed = int(getattr(result, "bytes_freed", 0) or 0)
    size = f" ({format_bytes(freed)})" if freed else ""
    return f"{done} {target}{size}"


def format_duration(seconds: float) -> str:
    """Return a compact human duration, e.g. 12.0 → '12s', 412000.0 → '4d 18h'."""
    s = int(max(0.0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


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
