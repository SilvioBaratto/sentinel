"""
Source-blind example tests for issue #26:
  feat: declarative disk cleanup rule engine

All tests are derived from acceptance criteria only.  No implementation
source was read during authoring (TDD Red phase).

Assumed module layout — the implementation MUST export these names:

    sentinel.execute.cleanup_rule
        CleanupRule, older_than, name_in, always, default_rules

    sentinel.execute.disk_cleaner
        RuleBasedDiskCleaner

    sentinel.domain.value_objects
        ActionKind   (enum with at least TRASH and DELETE)
        ActionResult (named-tuple or dataclass)
        MachineState (enum with at least NORMAL, WARN, CRITICAL, DISK_LOW)

    sentinel.domain.protocols
        DiskCleaner  (structural protocol with clean(state) -> tuple[ActionResult,...])
"""

import pytest
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock
from hypothesis import given, strategies as st

from sentinel.execute.cleanup_rule import (
    CleanupRule,
    older_than,
    name_in,
    always,
    default_rules,
)
from sentinel.execute.disk_cleaner import RuleBasedDiskCleaner
from sentinel.domain.value_objects import ActionKind, ActionResult, MachineState
from sentinel.domain.protocols import DiskCleaner


# ---------------------------------------------------------------------------
# Builders — derive shapes from criteria, not from production code
# ---------------------------------------------------------------------------


def _path_guard(*, safe: bool = True) -> MagicMock:
    g = MagicMock()
    g.is_safe.return_value = safe
    return g


def _activity_guard(*, active: bool = False) -> MagicMock:
    g = MagicMock()
    g.is_active.return_value = active
    return g


def _make_cleaner(
    *,
    rules=(),
    glob_fn=None,
    path_guard=None,
    activity_guard=None,
    trasher=None,
    deleter=None,
) -> RuleBasedDiskCleaner:
    return RuleBasedDiskCleaner(
        rules=list(rules),
        glob_fn=glob_fn if glob_fn is not None else (lambda _: []),
        path_guard=path_guard if path_guard is not None else _path_guard(),
        activity_guard=activity_guard
        if activity_guard is not None
        else _activity_guard(),
        trasher=trasher if trasher is not None else MagicMock(),
        deleter=deleter if deleter is not None else MagicMock(),
    )


def _trash_rule(*, globs=("/**/*.tmp",), activity_guarded: bool = False) -> CleanupRule:
    return CleanupRule(
        name="test-trash-rule",
        globs=list(globs),
        predicate=always(),
        action=ActionKind.TRASH,
        reversible=True,
        activity_guarded=activity_guarded,
    )


def _delete_rule(
    *, globs=("/**/node_modules",), activity_guarded: bool = True
) -> CleanupRule:
    return CleanupRule(
        name="test-delete-rule",
        globs=list(globs),
        predicate=always(),
        action=ActionKind.DELETE,
        reversible=False,
        activity_guarded=activity_guarded,
    )


# ---------------------------------------------------------------------------
# AC1 — CleanupRule frozen dataclass with required fields
# ---------------------------------------------------------------------------


class TestCleanupRuleDataClass:
    def test_when_cleanup_rule_field_is_mutated_then_frozen_instance_error_is_raised(
        self,
    ):
        rule = _trash_rule()
        with pytest.raises((AttributeError, FrozenInstanceError)):
            rule.name = "mutated"  # type: ignore[misc]

    def test_when_cleanup_rule_is_created_with_trash_action_then_all_fields_are_accessible(
        self,
    ):
        pred = always()
        rule = CleanupRule(
            name="app-caches",
            globs=["~/Library/Caches/**"],
            predicate=pred,
            action=ActionKind.TRASH,
            reversible=True,
            activity_guarded=False,
        )
        assert rule.name == "app-caches"
        assert rule.globs == ["~/Library/Caches/**"]
        assert rule.predicate is pred
        assert rule.action == ActionKind.TRASH
        assert rule.reversible is True
        assert rule.activity_guarded is False

    def test_when_cleanup_rule_is_created_with_delete_action_then_correct_fields_are_stored(
        self,
    ):
        rule = _delete_rule()
        assert rule.action == ActionKind.DELETE
        assert rule.reversible is False
        assert rule.activity_guarded is True


# ---------------------------------------------------------------------------
# AC1 — Predicate factories (pure functions; no filesystem access)
# ---------------------------------------------------------------------------


class TestPredicateFactories:
    def test_when_always_is_applied_to_a_non_empty_path_then_true_is_returned(self):
        pred = always()
        assert pred("/arbitrary/path") is True

    def test_when_always_is_applied_to_an_empty_string_then_true_is_returned(self):
        pred = always()
        assert pred("") is True

    def test_when_name_in_and_path_basename_matches_one_of_the_names_then_true_is_returned(
        self,
    ):
        pred = name_in(["node_modules", ".next"])
        assert pred("/project/node_modules") is True

    def test_when_name_in_and_path_basename_does_not_match_any_name_then_false_is_returned(
        self,
    ):
        pred = name_in(["node_modules", ".next"])
        assert pred("/project/src") is False

    def test_when_name_in_receives_empty_name_list_then_false_is_returned_for_any_path(
        self,
    ):
        pred = name_in([])
        assert pred("/project/node_modules") is False

    def test_when_older_than_and_file_age_exceeds_threshold_in_days_then_true_is_returned(
        self,
    ):
        epoch = 1_000_000

        def mtime_fn(_):
            return epoch - 8 * 86400  # 8 days ago

        pred = older_than(7, mtime_fn=mtime_fn, clock=lambda: epoch)
        assert pred("/some/old/file") is True

    def test_when_older_than_and_file_age_is_below_threshold_in_days_then_false_is_returned(
        self,
    ):
        epoch = 1_000_000

        def mtime_fn(_):
            return epoch - 1 * 86400  # 1 day ago

        pred = older_than(7, mtime_fn=mtime_fn, clock=lambda: epoch)
        assert pred("/some/recent/file") is False

    def test_when_older_than_is_given_a_boundary_age_then_no_exception_is_raised(self):
        """The predicate must not raise at the exact threshold boundary; result may be True or False."""
        epoch = 1_000_000

        def mtime_fn(_):
            return epoch - 7 * 86400  # exactly 7 days

        pred = older_than(7, mtime_fn=mtime_fn, clock=lambda: epoch)
        result = pred("/boundary/file")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# AC1 — default_rules(config) covers caches / Downloads / build artifacts
# ---------------------------------------------------------------------------


class TestDefaultRules:
    def test_when_default_rules_is_called_then_at_least_one_cache_rule_with_trash_is_returned(
        self,
    ):
        config = MagicMock()
        rules = default_rules(config)
        cache_rules = [r for r in rules if "cache" in r.name.lower()]
        assert cache_rules, "expected at least one cache cleanup rule"
        assert all(r.action == ActionKind.TRASH for r in cache_rules)

    def test_when_default_rules_is_called_then_cache_rules_are_marked_reversible(self):
        config = MagicMock()
        rules = default_rules(config)
        cache_rules = [r for r in rules if "cache" in r.name.lower()]
        assert cache_rules
        assert all(r.reversible is True for r in cache_rules)

    def test_when_default_rules_is_called_then_at_least_one_downloads_rule_with_trash_is_returned(
        self,
    ):
        config = MagicMock()
        rules = default_rules(config)
        dl_rules = [r for r in rules if "download" in r.name.lower()]
        assert dl_rules, "expected at least one Downloads cleanup rule"
        assert all(r.action == ActionKind.TRASH for r in dl_rules)

    def test_when_default_rules_is_called_then_downloads_rules_are_marked_reversible(
        self,
    ):
        config = MagicMock()
        rules = default_rules(config)
        dl_rules = [r for r in rules if "download" in r.name.lower()]
        assert dl_rules
        assert all(r.reversible is True for r in dl_rules)

    def test_when_default_rules_is_called_then_at_least_one_delete_rule_for_build_artifacts_is_returned(
        self,
    ):
        config = MagicMock()
        rules = default_rules(config)
        delete_rules = [r for r in rules if r.action == ActionKind.DELETE]
        assert delete_rules, "expected at least one build-artifact delete rule"

    def test_when_default_rules_is_called_then_all_delete_rules_are_activity_guarded(
        self,
    ):
        config = MagicMock()
        rules = default_rules(config)
        delete_rules = [r for r in rules if r.action == ActionKind.DELETE]
        assert delete_rules
        assert all(r.activity_guarded is True for r in delete_rules), (
            "build-artifact delete rules must be activity-guarded per spec"
        )


# ---------------------------------------------------------------------------
# AC2 — RuleBasedDiskCleaner implements DiskCleaner protocol
# ---------------------------------------------------------------------------


class TestRuleBasedDiskCleanerInterface:
    def test_when_rule_based_disk_cleaner_is_instantiated_then_it_satisfies_disk_cleaner_protocol(
        self,
    ):
        cleaner = _make_cleaner()
        assert isinstance(cleaner, DiskCleaner)

    def test_when_clean_is_called_in_disk_low_state_then_a_tuple_is_returned(self):
        cleaner = _make_cleaner()
        result = cleaner.clean(MachineState.DISK_LOW)
        assert isinstance(result, tuple)

    def test_when_disk_low_and_a_safe_path_is_matched_then_result_contains_action_results(
        self,
    ):
        path = "/Library/Caches/SomeApp/cache.db"

        def glob_fn(_):
            return [path]

        trasher = MagicMock()

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=glob_fn,
            path_guard=_path_guard(safe=True),
            trasher=trasher,
        )
        result = cleaner.clean(MachineState.DISK_LOW)
        assert isinstance(result, tuple)
        assert len(result) > 0
        assert all(isinstance(r, ActionResult) for r in result)


# ---------------------------------------------------------------------------
# AC3 — Short-circuit gate: state != DISK_LOW → () with zero side-effects
# ---------------------------------------------------------------------------


class TestShortCircuitGate:
    @pytest.mark.parametrize(
        "state",
        [
            MachineState.NORMAL,
            MachineState.WARN,
            MachineState.CRITICAL,
        ],
    )
    def test_when_state_is_not_disk_low_then_empty_tuple_is_returned(self, state):
        cleaner = _make_cleaner(rules=[_trash_rule()])
        result = cleaner.clean(state)
        assert result == ()

    @pytest.mark.parametrize(
        "state",
        [
            MachineState.NORMAL,
            MachineState.WARN,
            MachineState.CRITICAL,
        ],
    )
    def test_when_state_is_not_disk_low_then_no_glob_path_guard_or_shim_is_called(
        self, state
    ):
        glob_fn = MagicMock()
        pg = _path_guard()
        ag = _activity_guard()
        trasher = MagicMock()
        deleter = MagicMock()

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=glob_fn,
            path_guard=pg,
            activity_guard=ag,
            trasher=trasher,
            deleter=deleter,
        )
        cleaner.clean(state)

        glob_fn.assert_not_called()
        pg.is_safe.assert_not_called()
        ag.is_active.assert_not_called()
        trasher.trash.assert_not_called()
        deleter.delete.assert_not_called()


# ---------------------------------------------------------------------------
# AC4 — Pipeline: glob → predicate → path_guard → activity_guard → dispatch
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_when_path_guard_returns_unsafe_then_trasher_is_not_called(self):
        path = "/unsafe/file.tmp"
        trasher = MagicMock()

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=False),
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        trasher.trash.assert_not_called()

    def test_when_path_guard_returns_unsafe_then_deleter_is_not_called(self):
        path = "/unsafe/node_modules"
        deleter = MagicMock()

        cleaner = _make_cleaner(
            rules=[_delete_rule()],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=False),
            deleter=deleter,
        )
        cleaner.clean(MachineState.DISK_LOW)
        deleter.delete.assert_not_called()

    def test_when_predicate_returns_false_then_path_is_not_dispatched(self):
        epoch = 1_000_000

        def mtime_fn(_):
            return epoch - 1 * 86400  # only 1 day old

        pred = older_than(7, mtime_fn=mtime_fn, clock=lambda: epoch)
        rule = CleanupRule(
            name="stale-downloads",
            globs=["~/Downloads/**"],
            predicate=pred,
            action=ActionKind.TRASH,
            reversible=True,
            activity_guarded=False,
        )
        trasher = MagicMock()

        cleaner = _make_cleaner(
            rules=[rule],
            glob_fn=lambda _: ["/Users/u/Downloads/recent.zip"],
            path_guard=_path_guard(safe=True),
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        trasher.trash.assert_not_called()

    def test_when_activity_guarded_and_project_is_active_then_deleter_is_not_called(
        self,
    ):
        deleter = MagicMock()

        cleaner = _make_cleaner(
            rules=[_delete_rule(activity_guarded=True)],
            glob_fn=lambda _: ["/projects/active/node_modules"],
            path_guard=_path_guard(safe=True),
            activity_guard=_activity_guard(active=True),
            deleter=deleter,
        )
        cleaner.clean(MachineState.DISK_LOW)
        deleter.delete.assert_not_called()

    def test_when_activity_guarded_and_project_is_idle_then_deleter_is_called_with_path(
        self,
    ):
        path = "/projects/idle/node_modules"
        deleter = MagicMock()

        cleaner = _make_cleaner(
            rules=[_delete_rule(activity_guarded=True)],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=True),
            activity_guard=_activity_guard(active=False),
            deleter=deleter,
        )
        cleaner.clean(MachineState.DISK_LOW)
        deleter.delete.assert_called_once_with(path)

    def test_when_rule_is_not_activity_guarded_and_path_is_safe_then_dispatch_occurs_regardless_of_activity(
        self,
    ):
        """Non-activity-guarded rules (e.g. caches) must not consult activity_guard at all."""
        path = "/Library/Caches/SomeApp"
        trasher = MagicMock()
        ag = _activity_guard(active=True)  # active project — must NOT block cache rule

        cleaner = _make_cleaner(
            rules=[_trash_rule(activity_guarded=False)],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=True),
            activity_guard=ag,
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        trasher.trash.assert_called_once_with(path)


# ---------------------------------------------------------------------------
# AC5 — Caches/Downloads → Trash (REVERSIBLE); build artifacts routing
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    def test_when_action_is_trash_then_trasher_trash_is_called_and_deleter_is_not(self):
        path = "/Library/Caches/SomeApp/data"
        trasher = MagicMock()
        deleter = MagicMock()

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=True),
            trasher=trasher,
            deleter=deleter,
        )
        cleaner.clean(MachineState.DISK_LOW)
        trasher.trash.assert_called_once_with(path)
        deleter.delete.assert_not_called()

    def test_when_action_is_delete_and_project_is_idle_then_deleter_delete_is_called_and_trasher_is_not(
        self,
    ):
        path = "/projects/idle/node_modules"
        deleter = MagicMock()
        trasher = MagicMock()

        cleaner = _make_cleaner(
            rules=[_delete_rule(activity_guarded=True)],
            glob_fn=lambda _: [path],
            path_guard=_path_guard(safe=True),
            activity_guard=_activity_guard(active=False),
            deleter=deleter,
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        deleter.delete.assert_called_once_with(path)
        trasher.trash.assert_not_called()

    def test_when_action_is_delete_and_project_is_active_then_neither_shim_is_called(
        self,
    ):
        deleter = MagicMock()
        trasher = MagicMock()

        cleaner = _make_cleaner(
            rules=[_delete_rule(activity_guarded=True)],
            glob_fn=lambda _: ["/projects/active/node_modules"],
            path_guard=_path_guard(safe=True),
            activity_guard=_activity_guard(active=True),
            deleter=deleter,
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        deleter.delete.assert_not_called()
        trasher.trash.assert_not_called()

    def test_when_default_cache_rules_are_inspected_then_they_carry_trash_and_reversible_true(
        self,
    ):
        """Caches dispatched to Trash (REVERSIBLE) per spec feature 7."""
        config = MagicMock()
        rules = default_rules(config)
        cache_rules = [r for r in rules if "cache" in r.name.lower()]
        assert cache_rules
        for rule in cache_rules:
            assert rule.action == ActionKind.TRASH
            assert rule.reversible is True

    def test_when_default_delete_rules_are_inspected_then_they_are_activity_guarded(
        self,
    ):
        """Build artifacts permanent-delete must be activity-guarded per spec feature 7."""
        config = MagicMock()
        rules = default_rules(config)
        delete_rules = [r for r in rules if r.action == ActionKind.DELETE]
        assert delete_rules
        for rule in delete_rules:
            assert rule.activity_guarded is True


# ---------------------------------------------------------------------------
# AC6 — Per-path error isolation; clean() is a total function (never raises)
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_when_trasher_raises_for_one_path_then_remaining_paths_are_still_processed(
        self,
    ):
        paths = ["/a/file.tmp", "/b/file.tmp", "/c/file.tmp"]

        def flakey_trash(path: str) -> None:
            if path == "/b/file.tmp":
                raise OSError("simulated Trash failure")

        trasher = MagicMock()
        trasher.trash.side_effect = flakey_trash

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=lambda _: paths,
            path_guard=_path_guard(safe=True),
            trasher=trasher,
        )
        cleaner.clean(MachineState.DISK_LOW)
        assert trasher.trash.call_count == 3

    def test_when_deleter_raises_for_one_path_then_remaining_paths_are_still_processed(
        self,
    ):
        paths = [
            "/proj/a/node_modules",
            "/proj/b/node_modules",
            "/proj/c/node_modules",
        ]

        def flakey_delete(path: str) -> None:
            if "/b/" in path:
                raise RuntimeError("simulated delete failure")

        deleter = MagicMock()
        deleter.delete.side_effect = flakey_delete

        cleaner = _make_cleaner(
            rules=[_delete_rule()],
            glob_fn=lambda _: paths,
            path_guard=_path_guard(safe=True),
            activity_guard=_activity_guard(active=False),
            deleter=deleter,
        )
        cleaner.clean(MachineState.DISK_LOW)
        assert deleter.delete.call_count == 3

    def test_when_shim_raises_then_clean_does_not_propagate_the_exception(self):
        trasher = MagicMock()
        trasher.trash.side_effect = RuntimeError("boom")

        cleaner = _make_cleaner(
            rules=[_trash_rule()],
            glob_fn=lambda _: ["/some/path.tmp"],
            path_guard=_path_guard(safe=True),
            trasher=trasher,
        )
        result = cleaner.clean(MachineState.DISK_LOW)  # must not raise
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# Property-based tests — invariants derived from criteria
# ---------------------------------------------------------------------------


@given(st.text())
def test_when_always_predicate_is_applied_to_any_string_then_true_is_always_returned(
    path: str,
):
    """Invariant (AC1): always() is a constant-True total function over all strings."""
    assert always()(path) is True


@given(
    st.lists(st.text(max_size=64), max_size=20),
    st.text(max_size=128),
)
def test_when_name_in_predicate_is_called_twice_with_identical_args_then_same_result_is_returned(
    names: list, path: str
) -> None:
    """Idempotence (AC1): name_in(names)(path) is a pure, referentially-transparent function."""
    pred = name_in(names)
    first = pred(path)
    second = pred(path)
    assert first == second


@given(
    st.integers(min_value=0, max_value=365),
    st.integers(min_value=0, max_value=365),
    st.floats(
        min_value=0.0,
        max_value=float(730 * 86400),  # up to 730 days of age
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_when_older_than_threshold_is_stricter_then_result_is_subset_of_looser_threshold(
    days_base: int,
    extra_days: int,
    age_seconds: float,
) -> None:
    """Monotonicity (AC1): older_than(d+Δ, ...) ⊆ older_than(d, ...) for all Δ ≥ 0.

    A file that passes the stricter threshold must also pass the looser one.
    """
    epoch = 1_000_000_000.0

    def mtime_fn(_):
        return epoch - age_seconds

    def clock():
        return epoch

    pred_loose = older_than(days_base, mtime_fn=mtime_fn, clock=clock)
    pred_strict = older_than(days_base + extra_days, mtime_fn=mtime_fn, clock=clock)

    if pred_strict("/test-file"):
        assert pred_loose("/test-file"), (
            f"file passing older_than({days_base + extra_days} days) must also pass "
            f"older_than({days_base} days)"
        )


@given(st.sampled_from(list(MachineState)))
def test_when_clean_is_called_with_any_valid_machine_state_then_no_exception_is_raised(
    state: MachineState,
) -> None:
    """Invariant (AC6): clean() is a total function — it never propagates exceptions to callers."""
    cleaner = _make_cleaner(
        rules=[_trash_rule()],
        glob_fn=lambda _: [],
    )
    cleaner.clean(state)  # must not raise for any valid state
