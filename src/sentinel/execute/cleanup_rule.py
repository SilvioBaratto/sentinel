"""Declarative cleanup rules for disk cleanup (feature 7).

CleanupRule   — frozen data class: name + globs + predicate + action + metadata.
Predicate factories — pure, injected clock/mtime so tests stay deterministic.
default_rules — builds the canonical cache/Downloads/build-artifact rule set.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from sentinel.config import CleanupConfig
from sentinel.domain.value_objects import ActionKind


@dataclass(frozen=True)
class CleanupRule:
    """Immutable descriptor for one class of cleanable paths."""

    name: str
    globs: list[str]
    predicate: Callable[[str], bool]
    action: ActionKind
    reversible: bool
    activity_guarded: bool


# ── Predicate factories ───────────────────────────────────────────────────────


def always() -> Callable[[str], bool]:
    """Return a predicate that accepts every path unconditionally."""
    return lambda _: True


def name_in(names: list[str]) -> Callable[[str], bool]:
    """Return a predicate that matches when the path's basename is in *names*."""
    name_set = frozenset(names)
    return lambda path: os.path.basename(path) in name_set


def older_than(
    days: int,
    *,
    mtime_fn: Callable[[str], float],
    clock: Callable[[], float],
) -> Callable[[str], bool]:
    """Return a predicate that is True when the path is strictly older than *days* days."""
    threshold = days * 86400.0
    return lambda path: (clock() - mtime_fn(path)) > threshold


# ── Default rule set ──────────────────────────────────────────────────────────


def default_rules(config: CleanupConfig | None = None) -> list[CleanupRule]:
    """Build the canonical rule set from *config* (falls back to CleanupConfig defaults)."""
    cfg = config if isinstance(config, CleanupConfig) else CleanupConfig()
    return [_cache_rule(cfg), _downloads_rule(cfg), *_artifact_rules(cfg)]


def _cache_rule(cfg: CleanupConfig) -> CleanupRule:
    globs = list(cfg.cache_globs) or [os.path.expanduser("~/Library/Caches")]
    return CleanupRule(
        name="app-caches",
        globs=globs,
        predicate=always(),
        action=ActionKind.TRASH,
        reversible=True,
        activity_guarded=False,
    )


def _downloads_rule(cfg: CleanupConfig) -> CleanupRule:
    dl_dir = cfg.downloads_dir or os.path.expanduser("~/Downloads")
    return CleanupRule(
        name="stale-downloads",
        globs=[os.path.join(dl_dir, "*")],
        predicate=older_than(
            cfg.downloads_max_age_days, mtime_fn=os.path.getmtime, clock=time.time
        ),
        action=ActionKind.TRASH,
        reversible=True,
        activity_guarded=False,
    )


def _artifact_rules(cfg: CleanupConfig) -> list[CleanupRule]:
    arts = list(cfg.build_artifact_names)
    rule = CleanupRule(
        name="build-artifacts",
        globs=[f"**/{a}" for a in arts],
        predicate=name_in(arts),
        action=ActionKind.DELETE,
        reversible=False,
        activity_guarded=True,
    )
    return [rule]
