"""Source-blind example tests for Issue #19 — Cycle 3 domain types, protocols & config.

Authored from acceptance criteria only (Red phase). Every test will fail until
the corresponding implementation is written.

Conventions mirror tests/test_cycle2_domain.py.
"""

from __future__ import annotations

import dataclasses
import os

import pytest
from hypothesis import given, strategies as st

from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    AuditRecord,
    ExecutionMode,
    KillOutcome,
    KillStage,
    Reversibility,
)
from sentinel.domain.protocols import (
    ActivityGuard,
    AliveProbe,
    AppQuitter,
    AuditLogger,
    ContainerStopper,
    Deleter,
    DiskCleaner,
    Killer,
    Notifier,
    PathGuard,
    ProcessSignaler,
    Trasher,
)
from sentinel.config import (
    CleanupConfig,
    ExecuteConfig,
    KillConfig,
    NotifyConfig,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_GiB = 1024**3


def _action_result(**overrides) -> ActionResult:
    defaults = dict(
        kind=ActionKind.KILL_PROCESS,
        target="SomeApp",
        success=True,
        reversibility=Reversibility.PERMANENT,
    )
    defaults.update(overrides)
    return ActionResult(**defaults)


def _audit_record(**overrides) -> AuditRecord:
    defaults = dict(
        timestamp=0.0,
        kind=ActionKind.KILL_PROCESS,
        target="SomeApp",
        success=True,
        reversibility=Reversibility.PERMANENT,
        bytes_freed=0,
        mode=ExecutionMode.AUTO,
        detail="",
    )
    defaults.update(overrides)
    return AuditRecord(**defaults)


# ── Reversibility ─────────────────────────────────────────────────────────────


class TestReversibilityEnum:
    def test_when_reversibility_is_accessed_then_reversible_member_exists(self):
        assert hasattr(Reversibility, "REVERSIBLE")

    def test_when_reversibility_is_accessed_then_permanent_member_exists(self):
        assert hasattr(Reversibility, "PERMANENT")

    def test_when_reversibility_enum_is_inspected_then_it_has_exactly_two_members(self):
        assert len(list(Reversibility)) == 2


# ── ActionKind ────────────────────────────────────────────────────────────────


class TestActionKindEnum:
    def test_when_action_kind_is_accessed_then_kill_process_member_exists(self):
        assert hasattr(ActionKind, "KILL_PROCESS")

    def test_when_action_kind_is_accessed_then_stop_container_member_exists(self):
        assert hasattr(ActionKind, "STOP_CONTAINER")

    def test_when_action_kind_is_accessed_then_trash_member_exists(self):
        assert hasattr(ActionKind, "TRASH")

    def test_when_action_kind_is_accessed_then_delete_member_exists(self):
        assert hasattr(ActionKind, "DELETE")

    def test_when_action_kind_enum_is_inspected_then_it_has_exactly_four_members(self):
        assert len(list(ActionKind)) == 4


# ── ExecutionMode ─────────────────────────────────────────────────────────────


class TestExecutionModeEnum:
    def test_when_execution_mode_is_accessed_then_auto_member_exists(self):
        assert hasattr(ExecutionMode, "AUTO")

    def test_when_execution_mode_is_accessed_then_confirm_member_exists(self):
        assert hasattr(ExecutionMode, "CONFIRM")

    def test_when_execution_mode_is_accessed_then_dry_run_member_exists(self):
        assert hasattr(ExecutionMode, "DRY_RUN")

    def test_when_execution_mode_enum_is_inspected_then_it_has_exactly_three_members(
        self,
    ):
        assert len(list(ExecutionMode)) == 3


# ── KillStage ─────────────────────────────────────────────────────────────────


class TestKillStageEnum:
    def test_when_kill_stage_is_accessed_then_quit_member_exists(self):
        assert hasattr(KillStage, "QUIT")

    def test_when_kill_stage_is_accessed_then_sigterm_member_exists(self):
        assert hasattr(KillStage, "SIGTERM")

    def test_when_kill_stage_is_accessed_then_sigkill_member_exists(self):
        assert hasattr(KillStage, "SIGKILL")

    def test_when_kill_stage_is_accessed_then_none_member_exists(self):
        assert hasattr(KillStage, "NONE")

    def test_when_kill_stage_enum_is_inspected_then_it_has_exactly_four_members(self):
        assert len(list(KillStage)) == 4


# ── KillOutcome ───────────────────────────────────────────────────────────────


class TestKillOutcomeEnum:
    def test_when_kill_outcome_is_accessed_then_exited_member_exists(self):
        assert hasattr(KillOutcome, "EXITED")

    def test_when_kill_outcome_is_accessed_then_survived_member_exists(self):
        assert hasattr(KillOutcome, "SURVIVED")

    def test_when_kill_outcome_is_accessed_then_skipped_member_exists(self):
        assert hasattr(KillOutcome, "SKIPPED")

    def test_when_kill_outcome_is_accessed_then_error_member_exists(self):
        assert hasattr(KillOutcome, "ERROR")

    def test_when_kill_outcome_enum_is_inspected_then_it_has_exactly_four_members(self):
        assert len(list(KillOutcome)) == 4


# ── ActionResult ──────────────────────────────────────────────────────────────


class TestActionResultDefaults:
    def test_when_action_result_is_created_with_required_fields_then_bytes_freed_defaults_to_zero(
        self,
    ):
        assert _action_result().bytes_freed == 0

    def test_when_action_result_is_created_with_required_fields_then_detail_defaults_to_empty_string(
        self,
    ):
        assert _action_result().detail == ""

    def test_when_action_result_is_created_with_required_fields_then_dry_run_defaults_to_false(
        self,
    ):
        assert _action_result().dry_run is False

    def test_when_action_result_is_created_with_required_fields_then_outcome_defaults_to_none(
        self,
    ):
        assert _action_result().outcome is None

    def test_when_action_result_is_created_with_required_fields_then_stage_defaults_to_none(
        self,
    ):
        assert _action_result().stage is None


class TestActionResultFields:
    def test_when_action_result_is_created_then_kind_is_stored(self):
        assert _action_result(kind=ActionKind.TRASH).kind == ActionKind.TRASH

    def test_when_action_result_is_created_then_target_is_stored(self):
        assert (
            _action_result(target="~/Downloads/old.zip").target == "~/Downloads/old.zip"
        )

    def test_when_action_result_is_created_then_success_is_stored(self):
        assert _action_result(success=False).success is False

    def test_when_action_result_is_created_then_reversibility_is_stored(self):
        assert (
            _action_result(reversibility=Reversibility.REVERSIBLE).reversibility
            == Reversibility.REVERSIBLE
        )

    def test_when_action_result_is_created_then_it_is_a_dataclass_instance(self):
        assert dataclasses.is_dataclass(_action_result())


class TestActionResultFrozen:
    def test_when_action_result_attribute_is_mutated_then_an_error_is_raised(self):
        r = _action_result()
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            r.success = False  # type: ignore[misc]


# ── AuditRecord ───────────────────────────────────────────────────────────────


class TestAuditRecordFields:
    def test_when_audit_record_is_created_then_timestamp_is_stored(self):
        assert _audit_record(timestamp=9999.9).timestamp == pytest.approx(9999.9)

    def test_when_audit_record_is_created_then_mode_is_stored(self):
        assert _audit_record(mode=ExecutionMode.DRY_RUN).mode == ExecutionMode.DRY_RUN

    def test_when_audit_record_is_created_then_bytes_freed_is_stored(self):
        assert _audit_record(bytes_freed=4096).bytes_freed == 4096

    def test_when_audit_record_is_created_then_it_is_a_dataclass_instance(self):
        assert dataclasses.is_dataclass(_audit_record())


class TestAuditRecordFrozen:
    def test_when_audit_record_attribute_is_mutated_then_an_error_is_raised(self):
        r = _audit_record()
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            r.success = False  # type: ignore[misc]


# ── Protocols: runtime_checkable isinstance smoke ─────────────────────────────


class _FakeAppQuitter:
    def quit(self, pid: int, name: str) -> bool:
        return True


class _FakeProcessSignaler:
    def signal(self, pid: int, sig: int) -> bool:
        return True


class _FakeAliveProbe:
    def is_alive(self, pid: int) -> bool:
        return True


class _FakeKiller:
    def kill(self, candidate) -> ActionResult:
        return _action_result()


class _FakeContainerStopper:
    def stop(self, candidate) -> ActionResult:
        return _action_result()


class _FakePathGuard:
    def is_safe(self, path: str) -> bool:
        return True


class _FakeActivityGuard:
    def is_active(self, project_dir: str) -> bool:
        return False


class _FakeTrasher:
    def trash(self, path: str) -> ActionResult:
        return _action_result()


class _FakeDeleter:
    def delete(self, path: str) -> ActionResult:
        return _action_result()


class _FakeDiskCleaner:
    def clean(self, state) -> tuple:
        return ()


class _FakeAuditLogger:
    def record(self, record) -> None:
        pass


class _FakeNotifier:
    def notify(self, result) -> None:
        pass


class TestProtocolsRuntimeCheckable:
    def test_when_trivial_app_quitter_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeAppQuitter(), AppQuitter)

    def test_when_trivial_process_signaler_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeProcessSignaler(), ProcessSignaler)

    def test_when_trivial_alive_probe_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeAliveProbe(), AliveProbe)

    def test_when_trivial_killer_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeKiller(), Killer)

    def test_when_trivial_container_stopper_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeContainerStopper(), ContainerStopper)

    def test_when_trivial_path_guard_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakePathGuard(), PathGuard)

    def test_when_trivial_activity_guard_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeActivityGuard(), ActivityGuard)

    def test_when_trivial_trasher_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeTrasher(), Trasher)

    def test_when_trivial_deleter_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeDeleter(), Deleter)

    def test_when_trivial_disk_cleaner_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeDiskCleaner(), DiskCleaner)

    def test_when_trivial_audit_logger_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeAuditLogger(), AuditLogger)

    def test_when_trivial_notifier_is_given_then_isinstance_returns_true(self):
        assert isinstance(_FakeNotifier(), Notifier)

    def test_when_class_without_quit_is_given_then_app_quitter_isinstance_returns_false(
        self,
    ):
        class _NoMethods:
            pass

        assert not isinstance(_NoMethods(), AppQuitter)

    def test_when_class_without_is_safe_is_given_then_path_guard_isinstance_returns_false(
        self,
    ):
        class _NoMethods:
            pass

        assert not isinstance(_NoMethods(), PathGuard)

    def test_when_class_without_is_alive_is_given_then_alive_probe_isinstance_returns_false(
        self,
    ):
        class _NoMethods:
            pass

        assert not isinstance(_NoMethods(), AliveProbe)


# ── KillConfig ────────────────────────────────────────────────────────────────


class TestKillConfigDefaults:
    def test_when_kill_config_is_default_then_quit_grace_seconds_is_45(self):
        assert KillConfig().quit_grace_seconds == pytest.approx(45.0)

    def test_when_kill_config_is_default_then_sigterm_grace_seconds_is_20(self):
        assert KillConfig().sigterm_grace_seconds == pytest.approx(20.0)

    def test_when_kill_config_is_default_then_poll_interval_is_1(self):
        assert KillConfig().poll_interval == pytest.approx(1.0)

    def test_when_kill_config_is_default_then_critical_quit_grace_seconds_is_30(self):
        assert KillConfig().critical_quit_grace_seconds == pytest.approx(30.0)

    def test_when_kill_config_is_default_then_editor_auto_sigkill_is_false(self):
        assert KillConfig().editor_auto_sigkill is False

    def test_when_kill_config_is_default_then_editor_names_is_non_empty(self):
        assert len(KillConfig().editor_names) > 0

    def test_when_kill_config_is_default_then_editor_names_includes_a_known_editor(
        self,
    ):
        names = KillConfig().editor_names
        known = {"Code", "Visual Studio Code", "Cursor", "PyCharm", "IntelliJ IDEA"}
        assert bool(known & set(names))


class TestKillConfigFromMapping:
    def test_when_kill_config_from_mapping_overrides_quit_grace_then_new_value_is_returned(
        self,
    ):
        cfg = KillConfig.from_mapping({"quit_grace_seconds": 10.0})
        assert cfg.quit_grace_seconds == pytest.approx(10.0)

    def test_when_kill_config_from_mapping_overrides_one_field_then_other_fields_keep_defaults(
        self,
    ):
        cfg = KillConfig.from_mapping({"quit_grace_seconds": 10.0})
        assert cfg.sigterm_grace_seconds == pytest.approx(20.0)

    def test_when_kill_config_from_mapping_is_given_unknown_key_then_no_error_is_raised(
        self,
    ):
        KillConfig.from_mapping({"no_such_field": "ignored"})

    def test_when_kill_config_from_mapping_is_given_empty_dict_then_defaults_are_used(
        self,
    ):
        assert KillConfig.from_mapping({}).quit_grace_seconds == pytest.approx(45.0)


# ── CleanupConfig ─────────────────────────────────────────────────────────────


class TestCleanupConfigDefaults:
    def test_when_cleanup_config_is_default_then_disk_low_floor_is_20_gib(self):
        assert CleanupConfig().disk_low_floor == 20 * _GiB

    def test_when_cleanup_config_is_default_then_downloads_max_age_days_is_30(self):
        assert CleanupConfig().downloads_max_age_days == 30

    def test_when_cleanup_config_is_default_then_node_modules_is_in_build_artifact_names(
        self,
    ):
        assert "node_modules" in CleanupConfig().build_artifact_names

    def test_when_cleanup_config_is_default_then_next_is_in_build_artifact_names(self):
        assert ".next" in CleanupConfig().build_artifact_names

    def test_when_cleanup_config_is_default_then_dist_is_in_build_artifact_names(self):
        assert "dist" in CleanupConfig().build_artifact_names

    def test_when_cleanup_config_is_default_then_pycache_is_in_build_artifact_names(
        self,
    ):
        assert "__pycache__" in CleanupConfig().build_artifact_names

    def test_when_cleanup_config_is_default_then_derived_data_is_in_build_artifact_names(
        self,
    ):
        assert "DerivedData" in CleanupConfig().build_artifact_names

    def test_when_cleanup_config_is_default_then_deny_paths_includes_system(self):
        assert "/System" in CleanupConfig().deny_paths

    def test_when_cleanup_config_is_default_then_deny_paths_includes_library_application_support(
        self,
    ):
        deny = CleanupConfig().deny_paths
        expanded = os.path.expanduser("~/Library/Application Support")
        assert expanded in deny or "~/Library/Application Support" in deny


class TestCleanupConfigFromMapping:
    def test_when_cleanup_config_from_mapping_overrides_age_days_then_new_value_is_returned(
        self,
    ):
        cfg = CleanupConfig.from_mapping({"downloads_max_age_days": 60})
        assert cfg.downloads_max_age_days == 60

    def test_when_cleanup_config_from_mapping_is_given_unknown_key_then_no_error_is_raised(
        self,
    ):
        CleanupConfig.from_mapping({"no_such_field": "ignored"})


# ── NotifyConfig ──────────────────────────────────────────────────────────────


class TestNotifyConfigDefaults:
    def test_when_notify_config_is_default_then_enabled_is_true(self):
        assert NotifyConfig().enabled is True

    def test_when_notify_config_is_default_then_title_is_sentinel(self):
        assert NotifyConfig().title == "Sentinel"


class TestNotifyConfigFromMapping:
    def test_when_notify_config_from_mapping_sets_enabled_false_then_enabled_is_false(
        self,
    ):
        assert NotifyConfig.from_mapping({"enabled": False}).enabled is False

    def test_when_notify_config_from_mapping_is_given_unknown_key_then_no_error_is_raised(
        self,
    ):
        NotifyConfig.from_mapping({"no_such_field": "ignored"})

    def test_when_notify_config_from_mapping_is_given_empty_dict_then_title_defaults_to_sentinel(
        self,
    ):
        assert NotifyConfig.from_mapping({}).title == "Sentinel"


# ── ExecuteConfig ─────────────────────────────────────────────────────────────


class TestExecuteConfigDefaults:
    def test_when_execute_config_is_default_then_mode_is_auto(self):
        assert ExecuteConfig().mode == ExecutionMode.AUTO


class TestExecuteConfigFromMappingRecursive:
    def test_when_execute_config_from_mapping_passes_nested_kill_dict_then_kill_quit_grace_is_set(
        self,
    ):
        cfg = ExecuteConfig.from_mapping({"kill": {"quit_grace_seconds": 10.0}})
        assert cfg.kill.quit_grace_seconds == pytest.approx(10.0)

    def test_when_execute_config_from_mapping_passes_nested_kill_dict_then_other_kill_fields_keep_defaults(
        self,
    ):
        cfg = ExecuteConfig.from_mapping({"kill": {"quit_grace_seconds": 10.0}})
        assert cfg.kill.sigterm_grace_seconds == pytest.approx(20.0)

    def test_when_execute_config_from_mapping_passes_nested_notify_dict_then_enabled_is_overridden(
        self,
    ):
        cfg = ExecuteConfig.from_mapping({"notify": {"enabled": False}})
        assert cfg.notify.enabled is False

    def test_when_execute_config_from_mapping_passes_nested_cleanup_dict_then_age_days_is_overridden(
        self,
    ):
        cfg = ExecuteConfig.from_mapping({"cleanup": {"downloads_max_age_days": 60}})
        assert cfg.cleanup.downloads_max_age_days == 60

    def test_when_execute_config_from_mapping_is_given_unknown_key_then_no_error_is_raised(
        self,
    ):
        ExecuteConfig.from_mapping({"no_such_field": "ignored"})

    def test_when_execute_config_from_mapping_is_given_empty_dict_then_mode_defaults_to_auto(
        self,
    ):
        assert ExecuteConfig.from_mapping({}).mode == ExecutionMode.AUTO


# ── Property tests ────────────────────────────────────────────────────────────


@given(st.sampled_from(list(Reversibility)))
def test_when_reversibility_member_value_is_used_to_construct_then_original_member_is_returned(
    member,
):
    assert Reversibility(member.value) == member


@given(st.sampled_from(list(ActionKind)))
def test_when_action_kind_member_value_is_used_to_construct_then_original_member_is_returned(
    member,
):
    assert ActionKind(member.value) == member


@given(st.sampled_from(list(ExecutionMode)))
def test_when_execution_mode_member_value_is_used_to_construct_then_original_member_is_returned(
    member,
):
    assert ExecutionMode(member.value) == member


@given(st.sampled_from(list(KillStage)))
def test_when_kill_stage_member_value_is_used_to_construct_then_original_member_is_returned(
    member,
):
    assert KillStage(member.value) == member


@given(st.sampled_from(list(KillOutcome)))
def test_when_kill_outcome_member_value_is_used_to_construct_then_original_member_is_returned(
    member,
):
    assert KillOutcome(member.value) == member


@given(st.dictionaries(st.text(max_size=30), st.text(max_size=30)))
def test_when_kill_config_from_mapping_receives_any_string_dict_then_no_error_is_raised(
    mapping,
):
    KillConfig.from_mapping(mapping)


@given(st.dictionaries(st.text(max_size=30), st.text(max_size=30)))
def test_when_notify_config_from_mapping_receives_any_string_dict_then_no_error_is_raised(
    mapping,
):
    NotifyConfig.from_mapping(mapping)
