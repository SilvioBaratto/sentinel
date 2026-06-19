"""RuleBasedDiskCleaner — applies CleanupRules when state == DISK_LOW.

Short-circuit gate: returns () immediately for any non-DISK_LOW state, with no
side-effects on the glob function, path guard, activity guard, or shims.

Pipeline per path: expand glob → predicate filter → path_guard.is_safe (deny-list
wins) → if activity_guarded, skip active projects → dispatch to trasher/deleter.

Per-path try/except: one path failing never aborts the rest; clean() never raises.
"""

from __future__ import annotations

import glob as _glob
import os
from typing import Callable

from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    Reversibility,
    SentinelState,
)
from sentinel.execute.cleanup_rule import CleanupRule


def _project_of(path: str) -> str:
    """Return the parent directory of *path* as the project root."""
    return os.path.dirname(path)


def _fallback_result(path: str, rule: CleanupRule, *, success: bool) -> ActionResult:
    rev = Reversibility.REVERSIBLE if rule.reversible else Reversibility.PERMANENT
    return ActionResult(
        kind=rule.action, target=path, success=success, reversibility=rev
    )


class RuleBasedDiskCleaner:
    """Apply a list of CleanupRules and return one ActionResult per acted-on path."""

    def __init__(
        self,
        rules: list[CleanupRule],
        path_guard,
        activity_guard,
        trasher,
        deleter,
        glob_fn: Callable[[str], list[str]] = _glob.glob,
    ) -> None:
        self._rules = rules
        self._path_guard = path_guard
        self._activity_guard = activity_guard
        self._trasher = trasher
        self._deleter = deleter
        self._glob_fn = glob_fn

    def clean(self, state: SentinelState) -> tuple[ActionResult, ...]:
        if state != SentinelState.DISK_LOW:
            return ()
        return tuple(r for rule in self._rules for r in self._apply_rule(rule))

    # ── per-rule helpers ──────────────────────────────────────────────────────

    def _apply_rule(self, rule: CleanupRule) -> list[ActionResult]:
        results: list[ActionResult] = []
        for pattern in rule.globs:
            for path in self._safe_glob(pattern):
                result = self._process_path(path, rule)
                if result is not None:
                    results.append(result)
        return results

    def _safe_glob(self, pattern: str) -> list[str]:
        try:
            return self._glob_fn(pattern)
        except Exception:
            return []

    def _process_path(self, path: str, rule: CleanupRule) -> ActionResult | None:
        try:
            return self._evaluate(path, rule)
        except Exception:
            return None

    def _evaluate(self, path: str, rule: CleanupRule) -> ActionResult | None:
        if not rule.predicate(path):
            return None
        if not self._path_guard.is_safe(path):
            return None
        if rule.activity_guarded and self._activity_guard.is_active(_project_of(path)):
            return None
        return self._dispatch(path, rule)

    def _dispatch(self, path: str, rule: CleanupRule) -> ActionResult:
        if rule.action == ActionKind.TRASH:
            result = self._trasher.trash(path)
        else:
            result = self._deleter.delete(path)
        return (
            result
            if isinstance(result, ActionResult)
            else _fallback_result(path, rule, success=True)
        )
