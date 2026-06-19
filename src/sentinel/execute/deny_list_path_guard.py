"""DenyListPathGuard — hard deny-list gate for disk cleanup paths.

Implements PathGuard: is_safe(path) -> bool.
Fail-safe: any exception during normalization → False (protect-on-ambiguity).
"""

from __future__ import annotations

import os

from sentinel.config import CleanupConfig


class DenyListPathGuard:
    """Pure predicate: True only when path is outside all deny prefixes and not an .app bundle."""

    def __init__(
        self,
        config: CleanupConfig | None = None,
        home: str | None = None,
    ) -> None:
        self._home = home or os.path.expanduser("~")
        src = config.deny_paths if config is not None else CleanupConfig().deny_paths
        self._prefixes = _resolve_prefixes(src, self._home)

    def is_safe(self, path: str) -> bool:
        try:
            return self._evaluate(path)
        except Exception:
            return False

    def _evaluate(self, path: str) -> bool:
        if not path:
            return False
        norm = _normalize(path, self._home)
        if norm == "/":
            return False
        return not (_has_app_bundle(norm) or _under_any_prefix(norm, self._prefixes))


# ── Pure helpers ──────────────────────────────────────────────────────────────


def _normalize(path: str, home: str) -> str:
    if path.startswith("~"):
        path = home + path[1:]
    return os.path.realpath(os.path.abspath(path))


def _has_app_bundle(path: str) -> bool:
    return any(part.endswith(".app") for part in path.split(os.sep))


def _under_any_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    sep = os.sep
    return any(path == p or path.startswith(p + sep) for p in prefixes)


def _resolve_prefixes(paths: tuple[str, ...], home: str) -> tuple[str, ...]:
    real_home = os.path.expanduser("~")
    resolved = []
    for p in paths:
        if p.startswith("~"):
            p = home + p[1:]
        elif real_home and (p == real_home or p.startswith(real_home + os.sep)):
            p = home + p[len(real_home) :]
        resolved.append(os.path.realpath(os.path.abspath(p)))
    return tuple(resolved)
