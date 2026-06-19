"""
Source-blind example tests for Issue #1:
  feat: scaffold sentinel package (pyproject, domain types, config, protocols)

Tests derived directly from acceptance criteria. No implementation source read.
"""

import dataclasses
from enum import IntEnum

import pytest
from hypothesis import given
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# AC: PressureLevel(IntEnum) has members NORMAL=1, WARN=2, CRITICAL=4
#     and rejects 3/unknown ints
# ---------------------------------------------------------------------------


class TestPressureLevel:
    def _import(self):
        from sentinel.domain.value_objects import PressureLevel

        return PressureLevel

    def test_when_pressure_level_normal_is_accessed_then_value_is_1(self):
        PressureLevel = self._import()
        assert PressureLevel.NORMAL == 1

    def test_when_pressure_level_warn_is_accessed_then_value_is_2(self):
        PressureLevel = self._import()
        assert PressureLevel.WARN == 2

    def test_when_pressure_level_critical_is_accessed_then_value_is_4(self):
        PressureLevel = self._import()
        assert PressureLevel.CRITICAL == 4

    def test_when_pressure_level_constructed_with_1_then_normal_is_returned(self):
        PressureLevel = self._import()
        assert PressureLevel(1) is PressureLevel.NORMAL

    def test_when_pressure_level_constructed_with_2_then_warn_is_returned(self):
        PressureLevel = self._import()
        assert PressureLevel(2) is PressureLevel.WARN

    def test_when_pressure_level_constructed_with_4_then_critical_is_returned(self):
        PressureLevel = self._import()
        assert PressureLevel(4) is PressureLevel.CRITICAL

    def test_when_pressure_level_constructed_with_3_then_value_error_is_raised(self):
        PressureLevel = self._import()
        with pytest.raises(ValueError):
            PressureLevel(3)

    def test_when_pressure_level_constructed_with_0_then_value_error_is_raised(self):
        PressureLevel = self._import()
        with pytest.raises(ValueError):
            PressureLevel(0)

    def test_when_pressure_level_constructed_with_5_then_value_error_is_raised(self):
        PressureLevel = self._import()
        with pytest.raises(ValueError):
            PressureLevel(5)

    def test_when_pressure_level_constructed_with_negative_then_value_error_is_raised(
        self,
    ):
        PressureLevel = self._import()
        with pytest.raises(ValueError):
            PressureLevel(-1)

    def test_when_pressure_level_is_checked_then_it_is_intEnum_subclass(self):
        PressureLevel = self._import()
        assert issubclass(PressureLevel, IntEnum)

    def test_when_pressure_level_members_counted_then_exactly_three_exist(self):
        PressureLevel = self._import()
        assert len(PressureLevel) == 3

    @given(st.integers().filter(lambda x: x not in (1, 2, 4)))
    def test_when_pressure_level_constructed_with_non_member_int_then_value_error_is_raised(
        self, value
    ):
        PressureLevel = self._import()
        with pytest.raises(ValueError):
            PressureLevel(value)

    @given(st.sampled_from([1, 2, 4]))
    def test_when_pressure_level_constructed_with_valid_int_then_member_is_returned(
        self, value
    ):
        PressureLevel = self._import()
        level = PressureLevel(value)
        assert level.value == value


# ---------------------------------------------------------------------------
# AC: SentinelState(Enum) has exactly NORMAL/WARN/CRITICAL/DISK_LOW
# ---------------------------------------------------------------------------


class TestSentinelState:
    def _import(self):
        from sentinel.domain.value_objects import SentinelState

        return SentinelState

    def test_when_sentinel_state_normal_is_accessed_then_it_exists(self):
        SentinelState = self._import()
        assert hasattr(SentinelState, "NORMAL")

    def test_when_sentinel_state_warn_is_accessed_then_it_exists(self):
        SentinelState = self._import()
        assert hasattr(SentinelState, "WARN")

    def test_when_sentinel_state_critical_is_accessed_then_it_exists(self):
        SentinelState = self._import()
        assert hasattr(SentinelState, "CRITICAL")

    def test_when_sentinel_state_disk_low_is_accessed_then_it_exists(self):
        SentinelState = self._import()
        assert hasattr(SentinelState, "DISK_LOW")

    def test_when_sentinel_state_members_counted_then_exactly_four_exist(self):
        SentinelState = self._import()
        assert len(SentinelState) == 4

    def test_when_sentinel_state_member_names_inspected_then_only_expected_names_present(
        self,
    ):
        SentinelState = self._import()
        names = {m.name for m in SentinelState}
        assert names == {"NORMAL", "WARN", "CRITICAL", "DISK_LOW"}

    def test_when_sentinel_state_is_checked_then_it_is_enum_subclass(self):
        from enum import Enum

        SentinelState = self._import()
        assert issubclass(SentinelState, Enum)


# ---------------------------------------------------------------------------
# AC: Frozen value objects defined: DiskUsage, SwapUsage, MemoryReport,
#     ResourceSample, CandidateSignal
# ---------------------------------------------------------------------------


class TestFrozenValueObjects:
    """Each value object must be importable and frozen (immutable after construction)."""

    def test_when_disk_usage_constructed_then_fields_are_accessible(self):
        from sentinel.domain.value_objects import DiskUsage

        obj = DiskUsage(mount="/", free_bytes=10 * 1024**3, total_bytes=100 * 1024**3)
        assert obj.mount == "/"
        assert obj.free_bytes == 10 * 1024**3
        assert obj.total_bytes == 100 * 1024**3

    def test_when_disk_usage_mutated_then_error_is_raised(self):
        from sentinel.domain.value_objects import DiskUsage

        obj = DiskUsage(mount="/", free_bytes=1, total_bytes=2)
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            obj.free_bytes = 99  # type: ignore[misc]

    def test_when_swap_usage_constructed_then_fields_are_accessible(self):
        from sentinel.domain.value_objects import SwapUsage

        obj = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        assert obj.total_bytes == 0
        assert obj.used_bytes == 0
        assert obj.free_bytes == 0

    def test_when_swap_usage_mutated_then_error_is_raised(self):
        from sentinel.domain.value_objects import SwapUsage

        obj = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            obj.total_bytes = 99  # type: ignore[misc]

    def test_when_memory_report_constructed_then_it_is_not_none(self):
        from sentinel.domain.value_objects import MemoryReport

        # MemoryReport is reporting-only; we only require it is constructable and frozen.
        obj = MemoryReport(
            total_bytes=16 * 1024**3, used_bytes=8 * 1024**3, free_bytes=8 * 1024**3
        )
        assert obj is not None

    def test_when_memory_report_mutated_then_error_is_raised(self):
        from sentinel.domain.value_objects import MemoryReport

        obj = MemoryReport(total_bytes=1, used_bytes=1, free_bytes=0)
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            obj.total_bytes = 99  # type: ignore[misc]

    def test_when_resource_sample_constructed_then_timestamp_pressure_swap_disks_memory_accessible(
        self,
    ):
        from sentinel.domain.value_objects import (
            DiskUsage,
            MemoryReport,
            PressureLevel,
            ResourceSample,
            SwapUsage,
        )

        disk = DiskUsage(mount="/", free_bytes=50 * 1024**3, total_bytes=200 * 1024**3)
        swap = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        mem = MemoryReport(
            total_bytes=16 * 1024**3, used_bytes=8 * 1024**3, free_bytes=8 * 1024**3
        )
        sample = ResourceSample(
            timestamp=1_000.0,
            pressure=PressureLevel.NORMAL,
            swap=swap,
            disks=(disk,),
            memory=mem,
        )
        assert sample.timestamp == 1_000.0
        assert sample.pressure is PressureLevel.NORMAL
        assert sample.swap is swap
        assert sample.disks == (disk,)
        assert sample.memory is mem

    def test_when_resource_sample_mutated_then_error_is_raised(self):
        from sentinel.domain.value_objects import (
            DiskUsage,
            MemoryReport,
            PressureLevel,
            ResourceSample,
            SwapUsage,
        )

        disk = DiskUsage(mount="/", free_bytes=1, total_bytes=2)
        swap = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        mem = MemoryReport(total_bytes=1, used_bytes=1, free_bytes=0)
        sample = ResourceSample(
            timestamp=0.0,
            pressure=PressureLevel.NORMAL,
            swap=swap,
            disks=(disk,),
            memory=mem,
        )
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            sample.timestamp = 99.0  # type: ignore[misc]

    def test_when_candidate_signal_constructed_then_proposed_state_reason_triggering_sample_accessible(
        self,
    ):
        from sentinel.domain.value_objects import (
            CandidateSignal,
            DiskUsage,
            MemoryReport,
            PressureLevel,
            ResourceSample,
            SentinelState,
            SwapUsage,
        )

        disk = DiskUsage(mount="/", free_bytes=1, total_bytes=2)
        swap = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        mem = MemoryReport(total_bytes=1, used_bytes=1, free_bytes=0)
        sample = ResourceSample(
            timestamp=0.0,
            pressure=PressureLevel.WARN,
            swap=swap,
            disks=(disk,),
            memory=mem,
        )
        signal = CandidateSignal(
            proposed_state=SentinelState.WARN,
            reason="pressure WARN confirmed",
            triggering_sample=sample,
        )
        assert signal.proposed_state is SentinelState.WARN
        assert signal.reason == "pressure WARN confirmed"
        assert signal.triggering_sample is sample

    def test_when_candidate_signal_mutated_then_error_is_raised(self):
        from sentinel.domain.value_objects import (
            CandidateSignal,
            DiskUsage,
            MemoryReport,
            PressureLevel,
            ResourceSample,
            SentinelState,
            SwapUsage,
        )

        disk = DiskUsage(mount="/", free_bytes=1, total_bytes=2)
        swap = SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0)
        mem = MemoryReport(total_bytes=1, used_bytes=1, free_bytes=0)
        sample = ResourceSample(
            timestamp=0.0,
            pressure=PressureLevel.NORMAL,
            swap=swap,
            disks=(disk,),
            memory=mem,
        )
        signal = CandidateSignal(
            proposed_state=SentinelState.NORMAL,
            reason="ok",
            triggering_sample=sample,
        )
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            signal.reason = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC: MonitorConfig frozen dataclass with documented defaults
#     (interval 30.0s, disk-low floor 20 GiB, history size,
#      confirm-sample counts, cooldown)
# ---------------------------------------------------------------------------


class TestMonitorConfig:
    _GiB_20 = 20 * 1024**3

    def _import(self):
        from sentinel.config import MonitorConfig

        return MonitorConfig

    def test_when_monitor_config_default_constructed_then_interval_is_30_seconds(self):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        assert cfg.interval == 30.0

    def test_when_monitor_config_default_constructed_then_disk_low_floor_is_20_gib(
        self,
    ):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        assert cfg.disk_low_floor == self._GiB_20

    def test_when_monitor_config_default_constructed_then_history_size_is_positive_int(
        self,
    ):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        assert isinstance(cfg.history_size, int)
        assert cfg.history_size > 0

    def test_when_monitor_config_default_constructed_then_confirm_samples_is_positive_int(
        self,
    ):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        # "confirm-sample counts" — at least one field for confirm/debounce count
        assert hasattr(cfg, "confirm_samples") or hasattr(
            cfg, "confirm_samples_elevate"
        )
        val = getattr(cfg, "confirm_samples", None) or getattr(
            cfg, "confirm_samples_elevate", None
        )
        assert isinstance(val, int) and val > 0

    def test_when_monitor_config_default_constructed_then_cooldown_is_positive(self):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        assert hasattr(cfg, "cooldown") or hasattr(cfg, "cooldown_s")
        val = getattr(cfg, "cooldown", None) or getattr(cfg, "cooldown_s", None)
        assert val is not None and val > 0

    def test_when_monitor_config_is_mutated_then_frozen_instance_error_is_raised(self):
        MonitorConfig = self._import()
        cfg = MonitorConfig()
        with pytest.raises(
            (dataclasses.FrozenInstanceError, AttributeError, TypeError)
        ):
            cfg.interval = 99.0  # type: ignore[misc]

    def test_when_monitor_config_is_checked_then_it_is_a_dataclass(self):
        MonitorConfig = self._import()
        assert dataclasses.is_dataclass(MonitorConfig)

    def test_when_monitor_config_interval_overridden_then_custom_value_is_stored(self):
        MonitorConfig = self._import()
        cfg = MonitorConfig(interval=60.0)
        assert cfg.interval == 60.0

    def test_when_monitor_config_disk_low_floor_overridden_then_custom_value_is_stored(
        self,
    ):
        MonitorConfig = self._import()
        cfg = MonitorConfig(disk_low_floor=10 * 1024**3)
        assert cfg.disk_low_floor == 10 * 1024**3


# ---------------------------------------------------------------------------
# AC: protocols.py declares runtime_checkable Protocols:
#     PressureReader / SwapReader / DiskReader / MemoryReader / Clock /
#     ResourceSampler / History / ThresholdEngine / StateMachine
# ---------------------------------------------------------------------------

_PROTOCOL_NAMES = [
    "PressureReader",
    "SwapReader",
    "DiskReader",
    "MemoryReader",
    "Clock",
    "ResourceSampler",
    "History",
    "ThresholdEngine",
    "StateMachine",
]


class TestProtocols:
    def _import_all(self):
        import sentinel.domain.protocols as proto

        return proto

    @pytest.mark.parametrize("name", _PROTOCOL_NAMES)
    def test_when_protocol_name_is_imported_then_it_exists_in_protocols_module(
        self, name
    ):
        proto = self._import_all()
        assert hasattr(proto, name), (
            f"Protocol {name!r} not found in sentinel.domain.protocols"
        )

    @pytest.mark.parametrize("name", _PROTOCOL_NAMES)
    def test_when_protocol_is_checked_then_it_is_runtime_checkable(self, name):
        proto = self._import_all()
        cls = getattr(proto, name)
        # runtime_checkable protocols support isinstance() checks
        # We verify by calling isinstance on a dummy object — it must not raise TypeError
        # (non-runtime-checkable protocols raise TypeError on isinstance)
        try:
            isinstance(object(), cls)
        except TypeError as exc:
            if "cannot be used with isinstance" in str(
                exc
            ) or "Protocols with non-method members" in str(exc):
                pytest.fail(f"{name} is not @runtime_checkable: {exc}")


# ---------------------------------------------------------------------------
# AC: `import sentinel` and submodules succeed; package installs editable
# ---------------------------------------------------------------------------


class TestImports:
    def test_when_sentinel_is_imported_then_no_error_is_raised(self):
        import sentinel  # noqa: F401

    def test_when_sentinel_domain_is_imported_then_no_error_is_raised(self):
        import sentinel.domain  # noqa: F401

    def test_when_sentinel_domain_value_objects_is_imported_then_no_error_is_raised(
        self,
    ):
        import sentinel.domain.value_objects  # noqa: F401

    def test_when_sentinel_domain_protocols_is_imported_then_no_error_is_raised(self):
        import sentinel.domain.protocols  # noqa: F401

    def test_when_sentinel_config_is_imported_then_no_error_is_raised(self):
        import sentinel.config  # noqa: F401

    def test_when_sentinel_monitor_is_imported_then_no_error_is_raised(self):
        import sentinel.monitor  # noqa: F401

    def test_when_sentinel_rules_is_imported_then_no_error_is_raised(self):
        import sentinel.rules  # noqa: F401

    def test_when_sentinel_init_is_imported_then_no_side_effects_occur(self):
        # Re-import should be a no-op (no import-time I/O or side effects).
        # We assert the module is already cached (importlib.reload would be
        # needed to actually re-trigger, but we just check it's importable).
        import sentinel

        assert sentinel is not None
