"""Source-blind example tests for issue #10.

Authored directly from acceptance criteria — no implementation source was read.
All tests are in Red phase: they fail until the implementation is complete.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Helpers: import paths derived solely from criteria text
# ---------------------------------------------------------------------------


def _import_value_objects():
    from sentinel.domain import value_objects

    return value_objects


def _import_protocols():
    from sentinel.domain import protocols

    return protocols


def _import_config():
    import sentinel.config as cfg

    return cfg


# ---------------------------------------------------------------------------
# ProcessProtection enum
# ---------------------------------------------------------------------------


class TestProcessProtection:
    def test_when_protection_enum_imported_then_protected_member_exists(self):
        vo = _import_value_objects()
        assert hasattr(vo, "ProcessProtection")
        assert hasattr(vo.ProcessProtection, "PROTECTED")

    def test_when_protection_enum_imported_then_reapable_member_exists(self):
        vo = _import_value_objects()
        assert hasattr(vo.ProcessProtection, "REAPABLE")

    def test_when_process_protection_default_then_protected_is_safe_default(self):
        """PROTECTED is the safe default — it should be the first / lowest member."""
        vo = _import_value_objects()
        members = list(vo.ProcessProtection)
        assert members[0] is vo.ProcessProtection.PROTECTED


# ---------------------------------------------------------------------------
# ProcessInfo value object
# ---------------------------------------------------------------------------


class TestProcessInfo:
    def _make(self, **overrides):
        vo = _import_value_objects()
        defaults = dict(
            pid=1,
            ppid=0,
            name="test",
            cmdline=("test",),
            has_tty=False,
            tty=None,
            pgid=None,
            cpu_percent=0.0,
            rss_bytes=0,
            create_time=None,
        )
        defaults.update(overrides)
        return vo.ProcessInfo(**defaults)

    def test_when_process_info_created_then_fields_are_accessible(self):
        info = self._make(pid=42, name="python", has_tty=True, tty="/dev/ttys001")
        assert info.pid == 42
        assert info.name == "python"
        assert info.has_tty is True
        assert info.tty == "/dev/ttys001"

    def test_when_process_info_created_then_cmdline_is_tuple(self):
        info = self._make(cmdline=("python", "script.py"))
        assert isinstance(info.cmdline, tuple)

    def test_when_process_info_tty_is_none_then_has_tty_false(self):
        """Criterion: tty: str|None — None is valid."""
        info = self._make(tty=None)
        assert info.tty is None

    def test_when_process_info_pgid_is_none_then_valid(self):
        info = self._make(pgid=None)
        assert info.pgid is None

    def test_when_process_info_create_time_is_none_then_valid(self):
        info = self._make(create_time=None)
        assert info.create_time is None

    def test_when_process_info_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        info = self._make()
        with pytest.raises(Exception):  # FrozenInstanceError is a subclass of Exception
            info.pid = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProcessClassification value object
# ---------------------------------------------------------------------------


class TestProcessClassification:
    def _make(self, **overrides):
        vo = _import_value_objects()
        defaults = dict(
            pid=1,
            name="test",
            protection=vo.ProcessProtection.PROTECTED,
            reason="test reason",
        )
        defaults.update(overrides)
        return vo.ProcessClassification(**defaults)

    def test_when_classification_created_then_fields_are_accessible(self):
        vo = _import_value_objects()
        cls = self._make(
            pid=5,
            name="Chrome",
            protection=vo.ProcessProtection.REAPABLE,
            reason="idle",
        )
        assert cls.pid == 5
        assert cls.name == "Chrome"
        assert cls.protection is vo.ProcessProtection.REAPABLE
        assert cls.reason == "idle"

    def test_when_classification_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        cls = self._make()
        with pytest.raises(Exception):
            cls.pid = 0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProcessCandidate value object
# ---------------------------------------------------------------------------


class TestProcessCandidate:
    def _make_info(self):
        vo = _import_value_objects()
        return vo.ProcessInfo(
            pid=1,
            ppid=0,
            name="test",
            cmdline=("test",),
            has_tty=False,
            tty=None,
            pgid=None,
            cpu_percent=0.0,
            rss_bytes=0,
            create_time=None,
        )

    def test_when_process_candidate_created_then_fields_are_accessible(self):
        vo = _import_value_objects()
        info = self._make_info()
        cand = vo.ProcessCandidate(
            info=info, idle_seconds=7300.0, cpu_percent=0.1, reason="idle"
        )
        assert cand.info is info
        assert cand.idle_seconds == 7300.0
        assert cand.cpu_percent == 0.1
        assert cand.reason == "idle"

    def test_when_process_candidate_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        vo = _import_value_objects()
        info = self._make_info()
        cand = vo.ProcessCandidate(
            info=info, idle_seconds=7300.0, cpu_percent=0.1, reason="idle"
        )
        with pytest.raises(Exception):
            cand.idle_seconds = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FrontmostApp value object
# ---------------------------------------------------------------------------


class TestFrontmostApp:
    def test_when_frontmost_app_created_with_all_none_then_valid(self):
        """Criterion: all fields are nullable."""
        vo = _import_value_objects()
        app = vo.FrontmostApp(bundle_id=None, name=None, pid=None)
        assert app.bundle_id is None
        assert app.name is None
        assert app.pid is None

    def test_when_frontmost_app_created_with_values_then_fields_are_accessible(self):
        vo = _import_value_objects()
        app = vo.FrontmostApp(bundle_id="com.apple.Safari", name="Safari", pid=1234)
        assert app.bundle_id == "com.apple.Safari"
        assert app.name == "Safari"
        assert app.pid == 1234

    def test_when_frontmost_app_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        vo = _import_value_objects()
        app = vo.FrontmostApp(bundle_id=None, name=None, pid=None)
        with pytest.raises(Exception):
            app.pid = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ContainerStats value object
# ---------------------------------------------------------------------------


class TestContainerStats:
    def _make(self, **overrides):
        vo = _import_value_objects()
        defaults = dict(
            container_id="abc123",
            name="clipcraft_api",
            cpu_percent=0.0,
            net_rx_bytes=0,
            net_tx_bytes=0,
            block_read_bytes=0,
            block_write_bytes=0,
        )
        defaults.update(overrides)
        return vo.ContainerStats(**defaults)

    def test_when_container_stats_created_then_all_fields_accessible(self):
        stats = self._make(
            container_id="c1",
            name="myapp",
            cpu_percent=0.3,
            net_rx_bytes=100,
            net_tx_bytes=200,
            block_read_bytes=50,
            block_write_bytes=75,
        )
        assert stats.container_id == "c1"
        assert stats.name == "myapp"
        assert stats.cpu_percent == 0.3
        assert stats.net_rx_bytes == 100
        assert stats.net_tx_bytes == 200
        assert stats.block_read_bytes == 50
        assert stats.block_write_bytes == 75

    def test_when_container_stats_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        stats = self._make()
        with pytest.raises(Exception):
            stats.cpu_percent = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ContainerCandidate value object
# ---------------------------------------------------------------------------


class TestContainerCandidate:
    def test_when_container_candidate_created_then_fields_accessible(self):
        vo = _import_value_objects()
        cand = vo.ContainerCandidate(
            name="clipcraft_api",
            container_id="abc123",
            idle_seconds=7300.0,
            cpu_percent=0.1,
            reason="idle",
        )
        assert cand.name == "clipcraft_api"
        assert cand.container_id == "abc123"
        assert cand.idle_seconds == 7300.0
        assert cand.cpu_percent == 0.1
        assert cand.reason == "idle"

    def test_when_container_candidate_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        vo = _import_value_objects()
        cand = vo.ContainerCandidate(
            name="x", container_id="y", idle_seconds=0.0, cpu_percent=0.0, reason="r"
        )
        with pytest.raises(Exception):
            cand.name = "z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DetectionResult value object
# ---------------------------------------------------------------------------


class TestDetectionResult:
    def test_when_detection_result_created_empty_then_tuples_accessible(self):
        vo = _import_value_objects()
        result = vo.DetectionResult(processes=(), containers=())
        assert result.processes == ()
        assert result.containers == ()

    def test_when_detection_result_processes_field_then_type_is_tuple(self):
        vo = _import_value_objects()
        result = vo.DetectionResult(processes=(), containers=())
        assert isinstance(result.processes, tuple)
        assert isinstance(result.containers, tuple)

    def test_when_detection_result_mutation_attempted_then_frozen_instance_error_raised(
        self,
    ):
        vo = _import_value_objects()
        result = vo.DetectionResult(processes=(), containers=())
        with pytest.raises(Exception):
            result.processes = (None,)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Protocols — runtime_checkable isinstance checks (trivial fakes)
# ---------------------------------------------------------------------------


class TestProtocols:
    """Criterion: a trivial fake satisfies isinstance(fake, Proto) for each protocol."""

    def test_when_fake_process_lister_then_isinstance_passes(self):
        from sentinel.domain.protocols import ProcessLister

        class _FakeLister:
            def list(self) -> tuple:
                return ()

        assert isinstance(_FakeLister(), ProcessLister)

    def test_when_fake_frontmost_app_reader_then_isinstance_passes(self):
        from sentinel.domain.protocols import FrontmostAppReader
        from sentinel.domain.value_objects import FrontmostApp

        class _FakeReader:
            def read(self):
                return FrontmostApp(bundle_id=None, name=None, pid=None)

        assert isinstance(_FakeReader(), FrontmostAppReader)

    def test_when_fake_hid_idle_reader_then_isinstance_passes(self):
        from sentinel.domain.protocols import HidIdleReader

        class _FakeHid:
            def read(self) -> float:
                return 0.0

        assert isinstance(_FakeHid(), HidIdleReader)

    def test_when_fake_process_classifier_then_isinstance_passes(self):
        from sentinel.domain.protocols import ProcessClassifier

        class _FakeClassifier:
            def classify(self, proc, index): ...

        assert isinstance(_FakeClassifier(), ProcessClassifier)

    def test_when_fake_process_idle_detector_then_isinstance_passes(self):
        from sentinel.domain.protocols import ProcessIdleDetector

        class _FakeDetector:
            def detect(self, state) -> tuple:
                return ()

        assert isinstance(_FakeDetector(), ProcessIdleDetector)

    def test_when_fake_container_stats_reader_then_isinstance_passes(self):
        from sentinel.domain.protocols import ContainerStatsReader

        class _FakeStatsReader:
            def read(self) -> tuple:
                return ()

        assert isinstance(_FakeStatsReader(), ContainerStatsReader)

    def test_when_fake_container_session_reader_then_isinstance_passes(self):
        from sentinel.domain.protocols import ContainerSessionReader

        class _FakeSessionReader:
            def active_session_names(self) -> frozenset:
                return frozenset()

        assert isinstance(_FakeSessionReader(), ContainerSessionReader)

    def test_when_fake_container_idle_detector_then_isinstance_passes(self):
        from sentinel.domain.protocols import ContainerIdleDetector

        class _FakeIdleDetector:
            def detect(self, state) -> tuple:
                return ()

        assert isinstance(_FakeIdleDetector(), ContainerIdleDetector)


# ---------------------------------------------------------------------------
# ProcessConfig frozen dataclass with from_mapping
# ---------------------------------------------------------------------------


class TestProcessConfig:
    def test_when_process_config_imported_then_exists(self):
        cfg = _import_config()
        assert hasattr(cfg, "ProcessConfig")

    def test_when_process_config_default_idle_cpu_percent_then_matches_constraints(
        self,
    ):
        """Criterion: idle_cpu_percent=1.0 per constraints table."""
        cfg = _import_config()
        assert cfg.ProcessConfig().idle_cpu_percent == 1.0

    def test_when_process_config_default_idle_seconds_then_matches_constraints(self):
        """Criterion: idle_seconds=7200.0 (2h) per constraints table."""
        cfg = _import_config()
        assert cfg.ProcessConfig().idle_seconds == 7200.0

    def test_when_process_config_default_cpu_sample_interval_then_matches_constraints(
        self,
    ):
        """Criterion: cpu_sample_interval=1.0 per constraints table."""
        cfg = _import_config()
        assert cfg.ProcessConfig().cpu_sample_interval == 1.0

    def test_when_process_config_default_use_nsworkspace_frontmost_then_false(self):
        """Criterion: use_nsworkspace_frontmost=False (opt-in flag off by default)."""
        cfg = _import_config()
        assert cfg.ProcessConfig().use_nsworkspace_frontmost is False

    def test_when_process_config_mutation_attempted_then_raises(self):
        cfg = _import_config()
        config = cfg.ProcessConfig()
        with pytest.raises(Exception):
            config.idle_cpu_percent = 99.0  # type: ignore[misc]

    def test_when_process_config_from_mapping_with_known_keys_then_overrides_applied(
        self,
    ):
        cfg = _import_config()
        result = cfg.ProcessConfig.from_mapping(
            {"idle_cpu_percent": 2.0, "idle_seconds": 3600.0}
        )
        assert result.idle_cpu_percent == 2.0
        assert result.idle_seconds == 3600.0

    def test_when_process_config_from_mapping_with_unknown_keys_then_ignored(self):
        """Criterion: from_mapping ignores unknown keys (mirrors MonitorConfig)."""
        cfg = _import_config()
        # Should not raise even with extra keys
        result = cfg.ProcessConfig.from_mapping(
            {"idle_cpu_percent": 0.5, "nonexistent_key": "value"}
        )
        assert result.idle_cpu_percent == 0.5

    def test_when_process_config_shell_session_markers_then_contains_required_values(
        self,
    ):
        """Criterion: shell_session_markers contains login, -zsh, -bash, zsh, bash, Terminal, iTerm2, tmux, screen, ssh."""
        cfg = _import_config()
        markers = cfg.ProcessConfig().shell_session_markers
        required = {
            "login",
            "-zsh",
            "-bash",
            "zsh",
            "bash",
            "Terminal",
            "iTerm2",
            "tmux",
            "screen",
            "ssh",
        }
        assert required.issubset(set(markers))


# ---------------------------------------------------------------------------
# DockerConfig frozen dataclass with from_mapping
# ---------------------------------------------------------------------------


class TestDockerConfig:
    def test_when_docker_config_imported_then_exists(self):
        cfg = _import_config()
        assert hasattr(cfg, "DockerConfig")

    def test_when_docker_config_default_idle_cpu_percent_then_matches_constraints(self):
        """Criterion: idle_cpu_percent=0.5 per constraints table."""
        cfg = _import_config()
        assert cfg.DockerConfig().idle_cpu_percent == 0.5

    def test_when_docker_config_default_idle_seconds_then_matches_constraints(self):
        """Criterion: idle_seconds=7200.0 (2h) per constraints table."""
        cfg = _import_config()
        assert cfg.DockerConfig().idle_seconds == 7200.0

    def test_when_docker_config_default_consecutive_polls_then_matches_constraints(
        self,
    ):
        """Criterion: consecutive_polls=3 per constraints table."""
        cfg = _import_config()
        assert cfg.DockerConfig().consecutive_polls == 3

    def test_when_docker_config_default_io_delta_epsilon_then_zero(self):
        """Criterion: io_delta_epsilon=0 per constraints table."""
        cfg = _import_config()
        assert cfg.DockerConfig().io_delta_epsilon == 0

    def test_when_docker_config_default_always_up_prefixes_then_contains_optimizer(
        self,
    ):
        """Criterion: always_up_prefixes=('optimizer_',) per constraints table."""
        cfg = _import_config()
        assert "optimizer_" in cfg.DockerConfig().always_up_prefixes

    def test_when_docker_config_default_always_up_suffixes_then_contains_db(self):
        """Criterion: always_up_suffixes=('_db',) per constraints table."""
        cfg = _import_config()
        assert "_db" in cfg.DockerConfig().always_up_suffixes

    def test_when_docker_config_mutation_attempted_then_raises(self):
        cfg = _import_config()
        config = cfg.DockerConfig()
        with pytest.raises(Exception):
            config.idle_cpu_percent = 99.0  # type: ignore[misc]

    def test_when_docker_config_from_mapping_with_known_keys_then_overrides_applied(
        self,
    ):
        cfg = _import_config()
        result = cfg.DockerConfig.from_mapping(
            {"idle_cpu_percent": 1.0, "consecutive_polls": 5}
        )
        assert result.idle_cpu_percent == 1.0
        assert result.consecutive_polls == 5

    def test_when_docker_config_from_mapping_with_unknown_keys_then_ignored(self):
        """Criterion: from_mapping ignores unknown keys (mirrors MonitorConfig)."""
        cfg = _import_config()
        result = cfg.DockerConfig.from_mapping(
            {"idle_cpu_percent": 0.3, "unknown_field": True}
        )
        assert result.idle_cpu_percent == 0.3


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestProcessConfigFromMappingProperty:
    @given(
        st.fixed_dictionaries(
            {
                "idle_cpu_percent": st.floats(
                    min_value=0.0, max_value=100.0, allow_nan=False
                ),
                "idle_seconds": st.floats(min_value=0.0, allow_nan=False),
                "cpu_sample_interval": st.floats(min_value=0.001, allow_nan=False),
            }
        )
    )
    def test_when_process_config_from_mapping_with_valid_keys_then_no_error_raised(
        self, mapping
    ):
        """Invariant: from_mapping never raises for any dict of valid typed values."""
        cfg = _import_config()
        cfg.ProcessConfig.from_mapping(mapping)

    @given(
        st.dictionaries(
            st.text(min_size=1),
            st.one_of(
                st.integers(), st.floats(allow_nan=False), st.text(), st.booleans()
            ),
        )
    )
    def test_when_process_config_from_mapping_with_arbitrary_keys_then_no_error_raised(
        self, mapping
    ):
        """Invariant: unknown keys in any mapping never cause from_mapping to raise."""
        cfg = _import_config()
        cfg.ProcessConfig.from_mapping(mapping)


class TestDockerConfigFromMappingProperty:
    @given(
        st.dictionaries(
            st.text(min_size=1),
            st.one_of(
                st.integers(), st.floats(allow_nan=False), st.text(), st.booleans()
            ),
        )
    )
    def test_when_docker_config_from_mapping_with_arbitrary_keys_then_no_error_raised(
        self, mapping
    ):
        """Invariant: unknown keys in any mapping never cause from_mapping to raise."""
        cfg = _import_config()
        cfg.DockerConfig.from_mapping(mapping)


class TestValueObjectsFrozenProperty:
    @given(st.integers(min_value=1, max_value=99999))
    def test_when_process_info_pid_set_on_frozen_instance_then_always_raises(self, pid):
        """Invariant: frozen dataclass always raises on attribute assignment."""
        vo = _import_value_objects()
        info = vo.ProcessInfo(
            pid=pid,
            ppid=0,
            name="p",
            cmdline=("p",),
            has_tty=False,
            tty=None,
            pgid=None,
            cpu_percent=0.0,
            rss_bytes=0,
            create_time=None,
        )
        with pytest.raises(Exception):
            info.pid = pid + 1  # type: ignore[misc]

    @given(st.one_of(st.none(), st.text(min_size=1)))
    def test_when_frontmost_app_created_with_any_nullable_bundle_id_then_field_preserved(
        self, bundle_id
    ):
        """Invariant: FrontmostApp stores any str-or-None bundle_id without modification."""
        vo = _import_value_objects()
        app = vo.FrontmostApp(bundle_id=bundle_id, name=None, pid=None)
        assert app.bundle_id == bundle_id
