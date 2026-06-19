"""Pure byte-string parser for Docker human-readable byte values.

Handles the SI decimal units docker stats emits: B / kB / MB / GB.
Longer suffixes are checked first so "kB" beats "B".
"""

from __future__ import annotations

_SUFFIXES: tuple[tuple[str, int], ...] = (
    ("GB", 1_000_000_000),
    ("MB", 1_000_000),
    ("kB", 1_000),
    ("B", 1),
)


def parse_bytes(s: str) -> int:
    """Parse a Docker byte string like "1.2kB" → integer bytes."""
    s = s.strip()
    for suffix, scale in _SUFFIXES:
        if s.endswith(suffix):
            return int(round(float(s[: -len(suffix)]) * scale))
    raise ValueError(f"Unrecognised byte string: {s!r}")


def parse_byte_pair(s: str) -> tuple[int, int]:
    """Parse a Docker "rx / tx" pair like "1.2kB / 3.4MB" → (int, int)."""
    left, right = s.split(" / ", 1)
    return parse_bytes(left), parse_bytes(right)
