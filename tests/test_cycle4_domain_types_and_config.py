"""
Source-blind example tests for issue #29:
  feat: Cycle 4 domain types, protocols & config — wake proxy, service, advisor, AppConfig

Every test is derived from the acceptance criteria text only; no implementation source was
read. Tests are written in the Red phase — they fail today and pass once the AC is met.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings, strategies as st


# ── Criterion: value_objects.py gains Cycle 4 section ───────────────────────


class TestPublishedPort:
    def test_when_constructed_with_required_fields_then_instance_is_returned(self):
        from sentinel.domain.value_objects import PublishedPort

        port = PublishedPort(host_ip="127.0.0.1", host_port=8080, container_port=80)

        assert port.host_ip == "127.0.0.1"
        assert port.host_port == 8080
        assert port.container_port == 80

    def test_when_protocol_not_specified_then_default_is_tcp(self):
        from sentinel.domain.value_objects import PublishedPort

        port = PublishedPort(host_ip="0.0.0.0", host_port=3000, container_port=3000)

        assert port.protocol == "tcp"

    def test_when_protocol_specified_explicitly_then_value_is_preserved(self):
        from sentinel.domain.value_objects import PublishedPort

        port = PublishedPort(
            host_ip="0.0.0.0", host_port=53, container_port=53, protocol="udp"
        )

        assert port.protocol == "udp"

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.domain.value_objects import PublishedPort

        port = PublishedPort(host_ip="127.0.0.1", host_port=8080, container_port=80)

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            port.host_port = 9999  # type: ignore[misc]


class TestStackPorts:
    def test_when_constructed_then_fields_are_accessible(self):
        from sentinel.domain.value_objects import StackPorts

        sp = StackPorts(
            stack="clipcraft",
            containers=("clipcraft_api", "clipcraft_frontend"),
            ports=(),
        )

        assert sp.stack == "clipcraft"
        assert sp.containers == ("clipcraft_api", "clipcraft_frontend")
        assert sp.ports == ()

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.domain.value_objects import StackPorts

        sp = StackPorts(stack="mystack", containers=(), ports=())

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sp.stack = "changed"  # type: ignore[misc]


class TestWakeRegistration:
    def test_when_constructed_then_fields_are_accessible(self):
        from sentinel.domain.value_objects import WakeRegistration

        reg = WakeRegistration(
            stack="clipcraft",
            ports=(),
            restart_command=("docker", "compose", "-p", "clipcraft", "up", "-d"),
        )

        assert reg.stack == "clipcraft"
        assert reg.ports == ()
        assert "docker" in reg.restart_command

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.domain.value_objects import WakeRegistration

        reg = WakeRegistration(stack="s", ports=(), restart_command=())

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            reg.stack = "other"  # type: ignore[misc]


class TestWakeOutcome:
    def test_when_restarted_member_accessed_then_it_exists(self):
        from sentinel.domain.value_objects import WakeOutcome

        assert WakeOutcome.RESTARTED is WakeOutcome.RESTARTED

    def test_when_already_running_member_accessed_then_it_exists(self):
        from sentinel.domain.value_objects import WakeOutcome

        assert WakeOutcome.ALREADY_RUNNING is WakeOutcome.ALREADY_RUNNING

    def test_when_restart_failed_member_accessed_then_it_exists(self):
        from sentinel.domain.value_objects import WakeOutcome

        assert WakeOutcome.RESTART_FAILED is WakeOutcome.RESTART_FAILED

    def test_when_health_timeout_member_accessed_then_it_exists(self):
        from sentinel.domain.value_objects import WakeOutcome

        assert WakeOutcome.HEALTH_TIMEOUT is WakeOutcome.HEALTH_TIMEOUT

    def test_when_all_members_enumerated_then_exactly_four_exist(self):
        from sentinel.domain.value_objects import WakeOutcome

        names = {m.name for m in WakeOutcome}

        assert names == {
            "RESTARTED",
            "ALREADY_RUNNING",
            "RESTART_FAILED",
            "HEALTH_TIMEOUT",
        }


class TestAdvisorRanking:
    def test_when_constructed_then_ordered_targets_are_accessible(self):
        from sentinel.domain.value_objects import AdvisorRanking

        ranking = AdvisorRanking(
            ordered_targets=("clipcraft_api", "clipcraft_frontend"),
            explanations={"clipcraft_api": "idle 3h", "clipcraft_frontend": "idle 4h"},
        )

        assert ranking.ordered_targets == ("clipcraft_api", "clipcraft_frontend")

    def test_when_constructed_then_explanations_are_accessible(self):
        from sentinel.domain.value_objects import AdvisorRanking

        ranking = AdvisorRanking(
            ordered_targets=("a",),
            explanations={"a": "high mem"},
        )

        assert ranking.explanations["a"] == "high mem"

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.domain.value_objects import AdvisorRanking

        ranking = AdvisorRanking(ordered_targets=(), explanations={})

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ranking.ordered_targets = ("x",)  # type: ignore[misc]


class TestStatusReport:
    def _make_report(self):
        from sentinel.domain.value_objects import (
            MemoryReport,
            PressureLevel,
            SentinelState,
            StatusReport,
            SwapUsage,
        )

        return StatusReport(
            pressure=PressureLevel.NORMAL,
            state=SentinelState.NORMAL,
            memory=MemoryReport(
                total_bytes=16 * 1024**3, used_bytes=8 * 1024**3, free_bytes=8 * 1024**3
            ),
            swap=SwapUsage(total_bytes=0, used_bytes=0, free_bytes=0),
            disks=(),
            recent_actions=(),
            idle_processes=(),
            idle_containers=(),
            wake_proxies=(),
        )

    def test_when_constructed_then_pressure_field_is_accessible(self):
        from sentinel.domain.value_objects import PressureLevel

        report = self._make_report()

        assert report.pressure == PressureLevel.NORMAL

    def test_when_constructed_then_state_field_is_accessible(self):
        from sentinel.domain.value_objects import SentinelState

        report = self._make_report()

        assert report.state == SentinelState.NORMAL

    def test_when_constructed_then_collection_fields_are_empty_tuples(self):
        report = self._make_report()

        assert report.disks == ()
        assert report.recent_actions == ()
        assert report.idle_processes == ()
        assert report.idle_containers == ()
        assert report.wake_proxies == ()

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.domain.value_objects import SentinelState

        report = self._make_report()

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            report.state = SentinelState.WARN  # type: ignore[misc]


# ── Criterion: protocols.py gains runtime_checkable Protocols ────────────────


class TestProtocolsAreRuntimeCheckable:
    """Each new Protocol must be @runtime_checkable so isinstance() works."""

    def test_when_config_store_implementor_checked_then_isinstance_returns_true(self):
        from sentinel.domain.protocols import ConfigStore

        class FakeStore:
            def load(self): ...

            def save(self, config): ...

            def paths(self): ...

        assert isinstance(FakeStore(), ConfigStore)

    def test_when_port_discoverer_implementor_checked_then_isinstance_returns_true(
        self,
    ):
        from sentinel.domain.protocols import PortDiscoverer

        class FakeDiscoverer:
            def discover(self, name: str): ...

        assert isinstance(FakeDiscoverer(), PortDiscoverer)

    def test_when_stack_restarter_implementor_checked_then_isinstance_returns_true(
        self,
    ):
        from sentinel.domain.protocols import StackRestarter

        class FakeRestarter:
            def restart(self, registration): ...

            def is_running(self, stack: str): ...

        assert isinstance(FakeRestarter(), StackRestarter)

    def test_when_health_gate_implementor_checked_then_isinstance_returns_true(self):
        from sentinel.domain.protocols import HealthGate

        class FakeGate:
            def wait_ready(self, port, timeout: float): ...

        assert isinstance(FakeGate(), HealthGate)

    def test_when_wake_proxy_manager_implementor_checked_then_isinstance_returns_true(
        self,
    ):
        from sentinel.domain.protocols import WakeProxyManager

        class FakeManager:
            def register(self, registration): ...

            def unregister(self, stack: str): ...

            def active(self): ...

            def stop_all(self): ...

        assert isinstance(FakeManager(), WakeProxyManager)

    def test_when_advisor_implementor_checked_then_isinstance_returns_true(self):
        from sentinel.domain.protocols import Advisor

        class FakeAdvisor:
            def rank(self, detection): ...

        assert isinstance(FakeAdvisor(), Advisor)

    def test_when_service_controller_implementor_checked_then_isinstance_returns_true(
        self,
    ):
        from sentinel.domain.protocols import ServiceController

        class FakeController:
            def install(self): ...

            def uninstall(self): ...

            def start(self): ...

            def stop(self): ...

            def status(self): ...

        assert isinstance(FakeController(), ServiceController)

    def test_when_status_provider_implementor_checked_then_isinstance_returns_true(
        self,
    ):
        from sentinel.domain.protocols import StatusProvider

        class FakeProvider:
            def build(self): ...

        assert isinstance(FakeProvider(), StatusProvider)


# ── Criterion: config.py gains frozen WakeProxyConfig, ServiceConfig, ────────
# ──            AdvisorConfig, SentinelPaths, AppConfig ──────────────────────


class TestAdvisorConfigDefaults:
    def test_when_instantiated_with_no_args_then_enabled_is_false(self):
        from sentinel.config import AdvisorConfig

        assert AdvisorConfig().enabled is False

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.config import AdvisorConfig

        cfg = AdvisorConfig()

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.enabled = True  # type: ignore[misc]


class TestWakeProxyConfigDefaults:
    def test_when_instantiated_with_no_args_then_bind_host_is_loopback(self):
        from sentinel.config import WakeProxyConfig

        assert WakeProxyConfig().bind_host == "127.0.0.1"

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.config import WakeProxyConfig

        cfg = WakeProxyConfig()

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.bind_host = "0.0.0.0"  # type: ignore[misc]


class TestServiceConfig:
    def test_when_instantiated_with_no_args_then_instance_is_returned(self):
        from sentinel.config import ServiceConfig

        assert ServiceConfig() is not None

    def test_when_field_is_mutated_then_frozen_error_is_raised(self):
        from sentinel.config import ServiceConfig

        cfg = ServiceConfig()

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.label = "changed"  # type: ignore[misc]


class TestAppConfigAggregation:
    def test_when_instantiated_with_no_args_then_monitor_sub_config_is_present(self):
        from sentinel.config import AppConfig, MonitorConfig

        assert isinstance(AppConfig().monitor, MonitorConfig)

    def test_when_instantiated_with_no_args_then_process_sub_config_is_present(self):
        from sentinel.config import AppConfig, ProcessConfig

        assert isinstance(AppConfig().process, ProcessConfig)

    def test_when_instantiated_with_no_args_then_docker_sub_config_is_present(self):
        from sentinel.config import AppConfig, DockerConfig

        assert isinstance(AppConfig().docker, DockerConfig)

    def test_when_instantiated_with_no_args_then_execute_sub_config_is_present(self):
        from sentinel.config import AppConfig, ExecuteConfig

        assert isinstance(AppConfig().execute, ExecuteConfig)

    def test_when_instantiated_with_no_args_then_wake_sub_config_is_present(self):
        from sentinel.config import AppConfig, WakeProxyConfig

        assert isinstance(AppConfig().wake, WakeProxyConfig)

    def test_when_instantiated_with_no_args_then_service_sub_config_is_present(self):
        from sentinel.config import AppConfig, ServiceConfig

        assert isinstance(AppConfig().service, ServiceConfig)

    def test_when_instantiated_with_no_args_then_advisor_sub_config_is_present(self):
        from sentinel.config import AppConfig, AdvisorConfig

        assert isinstance(AppConfig().advisor, AdvisorConfig)


# ── Criterion: AppConfig.from_mapping(AppConfig.to_mapping(x)) round-trips ──


class TestAppConfigRoundTrip:
    def test_when_default_config_is_round_tripped_then_result_equals_original(self):
        from sentinel.config import AppConfig

        original = AppConfig()
        restored = AppConfig.from_mapping(AppConfig.to_mapping(original))

        assert restored == original

    def test_when_advisor_enabled_is_set_then_round_trip_preserves_it(self):
        from sentinel.config import AdvisorConfig, AppConfig

        original = AppConfig(advisor=AdvisorConfig(enabled=True))
        restored = AppConfig.from_mapping(AppConfig.to_mapping(original))

        assert restored.advisor.enabled is True
        assert restored == original

    def test_when_bind_host_is_overridden_then_round_trip_preserves_it(self):
        from sentinel.config import AppConfig, WakeProxyConfig

        original = AppConfig(wake=WakeProxyConfig(bind_host="0.0.0.0"))
        restored = AppConfig.from_mapping(AppConfig.to_mapping(original))

        assert restored.wake.bind_host == "0.0.0.0"
        assert restored == original

    def test_when_mapping_contains_unknown_keys_then_from_mapping_ignores_them(self):
        """Mirrors the from_mapping filtering pattern in the existing sub-configs."""
        from sentinel.config import AppConfig

        mapping = AppConfig.to_mapping(AppConfig())
        mapping["nonexistent_top_level"] = "ignored"
        # Must not raise
        cfg = AppConfig.from_mapping(mapping)
        assert isinstance(cfg, AppConfig)

    def test_when_sub_config_mapping_contains_unknown_keys_then_defaults_are_used(self):
        from sentinel.config import AppConfig

        mapping = {"advisor": {"enabled": False, "totally_bogus_key": 42}}
        cfg = AppConfig.from_mapping(mapping)

        assert cfg.advisor.enabled is False

    # ── Property-based: round-trip invariant holds for all valid bool values ──

    @given(st.booleans())
    def test_when_advisor_enabled_varied_then_round_trip_preserves_the_value(
        self, enabled: bool
    ):
        """Invariant: advisor.enabled survives to_mapping → from_mapping unchanged."""
        from sentinel.config import AdvisorConfig, AppConfig

        original = AppConfig(advisor=AdvisorConfig(enabled=enabled))
        restored = AppConfig.from_mapping(AppConfig.to_mapping(original))

        assert restored.advisor.enabled == enabled

    @given(
        st.floats(
            min_value=1.0,
            max_value=120.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=50)
    def test_when_health_timeout_varied_then_round_trip_preserves_the_value(
        self, timeout: float
    ):
        """Invariant: WakeProxyConfig.health_timeout survives round-trip."""
        from sentinel.config import AppConfig, WakeProxyConfig

        original = AppConfig(wake=WakeProxyConfig(health_timeout=timeout))
        restored = AppConfig.from_mapping(AppConfig.to_mapping(original))

        assert restored.wake.health_timeout == timeout


# ── Criterion: SentinelPaths.default() resolves under Library paths ──────────


class TestSentinelPathsDefault:
    def test_when_default_called_then_config_path_is_under_application_support_sentinel(
        self,
    ):
        import os

        from sentinel.config import SentinelPaths

        paths = SentinelPaths.default()
        expected = os.path.expanduser("~/Library/Application Support/Sentinel/")

        assert paths.config_path.startswith(expected)

    def test_when_default_called_then_audit_log_path_is_under_application_support_sentinel(
        self,
    ):
        import os

        from sentinel.config import SentinelPaths

        paths = SentinelPaths.default()
        expected = os.path.expanduser("~/Library/Application Support/Sentinel/")

        assert paths.audit_log_path.startswith(expected)

    def test_when_default_called_then_state_path_is_under_application_support_sentinel(
        self,
    ):
        import os

        from sentinel.config import SentinelPaths

        paths = SentinelPaths.default()
        expected = os.path.expanduser("~/Library/Application Support/Sentinel/")

        assert paths.state_path.startswith(expected)

    def test_when_default_called_then_launch_agents_dir_is_under_library_launch_agents(
        self,
    ):
        import os

        from sentinel.config import SentinelPaths

        paths = SentinelPaths.default()
        expected = os.path.expanduser("~/Library/LaunchAgents")

        assert paths.launch_agents_dir.startswith(expected)

    def test_when_default_called_then_no_path_contains_unexpanded_tilde(self):
        from sentinel.config import SentinelPaths

        paths = SentinelPaths.default()

        assert "~" not in paths.config_path
        assert "~" not in paths.audit_log_path
        assert "~" not in paths.state_path
        assert "~" not in paths.launch_agents_dir


# ── Criterion: importing the modules spawns no subprocess, no asyncio/urllib/typer ──


class TestNoSideEffectsOnImport:
    def test_when_value_objects_imported_then_asyncio_not_in_module_namespace(self):
        import sentinel.domain.value_objects as vo

        assert "asyncio" not in vo.__dict__

    def test_when_value_objects_imported_then_urllib_not_in_module_namespace(self):
        import sentinel.domain.value_objects as vo

        assert "urllib" not in vo.__dict__

    def test_when_value_objects_imported_then_typer_not_in_module_namespace(self):
        import sentinel.domain.value_objects as vo

        assert "typer" not in vo.__dict__

    def test_when_protocols_imported_then_asyncio_not_in_module_namespace(self):
        import sentinel.domain.protocols as proto

        assert "asyncio" not in proto.__dict__

    def test_when_protocols_imported_then_typer_not_in_module_namespace(self):
        import sentinel.domain.protocols as proto

        assert "typer" not in proto.__dict__

    def test_when_config_imported_then_asyncio_not_in_module_namespace(self):
        import sentinel.config as cfg_mod

        assert "asyncio" not in cfg_mod.__dict__

    def test_when_config_imported_then_urllib_not_in_module_namespace(self):
        import sentinel.config as cfg_mod

        assert "urllib" not in cfg_mod.__dict__

    def test_when_config_imported_then_typer_not_in_module_namespace(self):
        import sentinel.config as cfg_mod

        assert "typer" not in cfg_mod.__dict__
