"""
Source-blind tests for Issue #17: detection composition root + pipeline integration.

Authored against acceptance criteria only — no implementation source was read.
All tests use scripted fakes injected via build_detection(config, detectors=...);
the real OS is never called.

Design assumptions (chosen as the simplest behaviour consistent with criteria):
  - The composition module lives at sentinel.detection.
  - build_detection(config, detectors={"process": ..., "container": ...}) mirrors
    build_pipeline(config, readers=...) from sentinel.pipeline.
  - DetectionPipeline.detect(state) -> DetectionResult.
  - detect(NORMAL) short-circuits before delegating to either inner detector.
  - The two inner detectors each receive the state and return their respective
    candidate tuples; the pipeline merges them into DetectionResult.

Skipped criteria (oracle: NOT VERIFIABLE):
  - All tests pass (boilerplate suite gate; no per-criterion assertion)
  - SOLID, clean code (subjective code-quality prose; no runtime assertion)
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from sentinel.config import MonitorConfig
from sentinel.domain.value_objects import (
    ContainerCandidate,
    DetectionResult,
    ProcessCandidate,
    ProcessInfo,
    SentinelState,
)


# ── lazy imports (contract surfaces only — no implementation read) ─────────────


def _build_detection():
    from sentinel.detection import build_detection  # noqa: PLC0415

    return build_detection


def _pipeline_cls():
    from sentinel.detection import DetectionPipeline  # noqa: PLC0415

    return DetectionPipeline


# ── constants ─────────────────────────────────────────────────────────────────

_TWO_HOURS: float = 7200.0
_GiB: int = 1024**3

_NON_NORMAL_STATES = [
    SentinelState.WARN,
    SentinelState.CRITICAL,
    SentinelState.DISK_LOW,
]


# ── domain builders ───────────────────────────────────────────────────────────


def _make_process_candidate(pid: int = 1, name: str = "Chrome") -> ProcessCandidate:
    info = ProcessInfo(
        pid=pid,
        ppid=0,
        name=name,
        cmdline=(name,),
        has_tty=False,
        tty=None,
        pgid=None,
        cpu_percent=0.2,
        rss_bytes=0,
        create_time=None,
        idle_seconds=_TWO_HOURS + 1.0,
    )
    return ProcessCandidate(
        info=info,
        idle_seconds=_TWO_HOURS + 1.0,
        cpu_percent=0.2,
        reason=f"{name} idle 2h01m, cpu 0.2%, not frontmost",
    )


def _make_container_candidate(name: str = "clipcraft_api") -> ContainerCandidate:
    return ContainerCandidate(
        name=name,
        container_id="deadbeef",
        idle_seconds=_TWO_HOURS + 60.0,
        cpu_percent=0.1,
        reason=f"{name} idle 2h01m, cpu 0.1%, no exec session",
    )


# ── fakes derived from acceptance-criteria text ───────────────────────────────


class _CallCountingProcessDetector:
    """
    Call-counting stub that satisfies ProcessIdleDetector.detect(state) -> tuple.

    detect() records each call state so tests can assert call counts and never-NORMAL
    delegation. kill_calls records any kill() invocations — must stay empty.
    """

    def __init__(self, candidates: tuple = ()) -> None:
        self._candidates = candidates
        self.detect_calls: list[SentinelState] = []
        self.kill_calls: list = []

    def detect(self, state: SentinelState) -> tuple[ProcessCandidate, ...]:
        self.detect_calls.append(state)
        return self._candidates

    def kill(self, *args, **kwargs) -> None:
        self.kill_calls.append((args, kwargs))

    @property
    def call_count(self) -> int:
        return len(self.detect_calls)


class _CallCountingContainerDetector:
    """
    Call-counting stub that satisfies ContainerIdleDetector.detect(state) -> tuple.

    stop_calls and delete_calls record any mutating invocations — must stay empty.
    """

    def __init__(self, candidates: tuple = ()) -> None:
        self._candidates = candidates
        self.detect_calls: list[SentinelState] = []
        self.stop_calls: list = []
        self.delete_calls: list = []

    def detect(self, state: SentinelState) -> tuple[ContainerCandidate, ...]:
        self.detect_calls.append(state)
        return self._candidates

    def stop(self, *args, **kwargs) -> None:
        self.stop_calls.append((args, kwargs))

    def delete(self, *args, **kwargs) -> None:
        self.delete_calls.append((args, kwargs))

    @property
    def call_count(self) -> int:
        return len(self.detect_calls)


# ── pipeline factory helper ───────────────────────────────────────────────────


def _make_pipeline(
    process_candidates: tuple = (),
    container_candidates: tuple = (),
) -> tuple[object, _CallCountingProcessDetector, _CallCountingContainerDetector]:
    """Build a DetectionPipeline with scripted fakes; returns (pipeline, proc_det, cont_det)."""
    build_detection = _build_detection()
    proc_det = _CallCountingProcessDetector(candidates=process_candidates)
    cont_det = _CallCountingContainerDetector(candidates=container_candidates)
    pipeline = build_detection(
        MonitorConfig(),
        detectors={"process": proc_det, "container": cont_det},
    )
    return pipeline, proc_det, cont_det


# ═══════════════════════════════════════════════════════════════════════════════
#  AC1 — DetectionPipeline.detect(state) -> DetectionResult
#         runs both detectors for non-NORMAL states; returns combined candidates
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionPipelineInterface:
    def test_when_build_detection_called_with_fakes_then_detection_pipeline_is_returned(
        self,
    ):
        """build_detection(config, detectors=...) returns a DetectionPipeline (AC1)."""
        pipeline, _, _ = _make_pipeline()
        assert isinstance(pipeline, _pipeline_cls())

    def test_when_detect_called_with_warn_then_detection_result_is_returned(self):
        """detect(WARN) -> DetectionResult (AC1 return type)."""
        pipeline, _, _ = _make_pipeline()
        result = pipeline.detect(SentinelState.WARN)
        assert isinstance(result, DetectionResult)

    def test_when_detect_called_with_critical_then_detection_result_is_returned(self):
        """detect(CRITICAL) -> DetectionResult (AC1 return type)."""
        pipeline, _, _ = _make_pipeline()
        result = pipeline.detect(SentinelState.CRITICAL)
        assert isinstance(result, DetectionResult)

    def test_when_detect_called_with_disk_low_then_detection_result_is_returned(self):
        """detect(DISK_LOW) -> DetectionResult (AC1 return type)."""
        pipeline, _, _ = _make_pipeline()
        result = pipeline.detect(SentinelState.DISK_LOW)
        assert isinstance(result, DetectionResult)

    def test_when_warn_state_then_process_detector_is_called_exactly_once(self):
        """AC1: DefaultProcessIdleDetector is invoked for non-NORMAL states."""
        pipeline, proc_det, _ = _make_pipeline()
        pipeline.detect(SentinelState.WARN)
        assert proc_det.call_count == 1

    def test_when_warn_state_then_container_detector_is_called_exactly_once(self):
        """AC1: DefaultContainerIdleDetector is invoked for non-NORMAL states."""
        pipeline, _, cont_det = _make_pipeline()
        pipeline.detect(SentinelState.WARN)
        assert cont_det.call_count == 1

    def test_when_non_normal_state_then_result_processes_contains_detector_output(self):
        """AC1: process candidates from the inner detector appear in DetectionResult.processes."""
        proc = _make_process_candidate(pid=7, name="Slack")
        pipeline, _, _ = _make_pipeline(process_candidates=(proc,))
        result = pipeline.detect(SentinelState.WARN)
        pids = [c.info.pid for c in result.processes]
        assert 7 in pids

    def test_when_non_normal_state_then_result_containers_contains_detector_output(
        self,
    ):
        """AC1: container candidates from the inner detector appear in DetectionResult.containers."""
        cont = _make_container_candidate("clipcraft_frontend")
        pipeline, _, _ = _make_pipeline(container_candidates=(cont,))
        result = pipeline.detect(SentinelState.WARN)
        names = [c.name for c in result.containers]
        assert "clipcraft_frontend" in names

    def test_when_both_detectors_return_candidates_then_result_contains_both(self):
        """AC1: combined candidates — processes and containers both present in result."""
        proc = _make_process_candidate(pid=1, name="Chrome")
        cont = _make_container_candidate("clipcraft_api")
        pipeline, _, _ = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        result = pipeline.detect(SentinelState.WARN)
        assert len(result.processes) >= 1
        assert len(result.containers) >= 1

    @pytest.mark.parametrize("state", _NON_NORMAL_STATES)
    def test_when_any_non_normal_state_then_both_detectors_called(
        self, state: SentinelState
    ):
        """AC1: both detectors run for every non-NORMAL state."""
        pipeline, proc_det, cont_det = _make_pipeline()
        pipeline.detect(state)
        assert proc_det.call_count == 1
        assert cont_det.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  AC2 — detect(NORMAL) → empty DetectionResult; no OS calls
#         (asserted via call-counting fakes)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalStateShortCircuit:
    def test_when_detect_normal_then_detection_result_is_returned(self):
        """detect(NORMAL) returns a DetectionResult, not None (AC2)."""
        pipeline, _, _ = _make_pipeline()
        result = pipeline.detect(SentinelState.NORMAL)
        assert isinstance(result, DetectionResult)

    def test_when_detect_normal_then_processes_are_empty(self):
        """detect(NORMAL) → processes == () even when process detector has candidates (AC2)."""
        proc = _make_process_candidate()
        pipeline, _, _ = _make_pipeline(process_candidates=(proc,))
        result = pipeline.detect(SentinelState.NORMAL)
        assert result.processes == ()

    def test_when_detect_normal_then_containers_are_empty(self):
        """detect(NORMAL) → containers == () even when container detector has candidates (AC2)."""
        cont = _make_container_candidate()
        pipeline, _, _ = _make_pipeline(container_candidates=(cont,))
        result = pipeline.detect(SentinelState.NORMAL)
        assert result.containers == ()

    def test_when_detect_normal_then_process_detector_is_not_called(self):
        """AC2: no OS calls — process detector (fake) must not be invoked on NORMAL."""
        pipeline, proc_det, _ = _make_pipeline(
            process_candidates=(_make_process_candidate(),),
        )
        pipeline.detect(SentinelState.NORMAL)
        assert proc_det.call_count == 0

    def test_when_detect_normal_then_container_detector_is_not_called(self):
        """AC2: no OS calls — container detector (fake) must not be invoked on NORMAL."""
        pipeline, _, cont_det = _make_pipeline(
            container_candidates=(_make_container_candidate(),),
        )
        pipeline.detect(SentinelState.NORMAL)
        assert cont_det.call_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  AC3 — build_detection(config) deferred imports
#         import sentinel.detection spawns no subprocess; no heavy adapter at load time
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeferredImports:
    def test_when_detection_module_imported_then_psutil_is_not_newly_added_to_sys_modules(
        self,
    ):
        """
        AC3: sentinel.detection must not eagerly import psutil at module-load time.

        Records sys.modules before and after importing the detection module and asserts
        psutil was NOT freshly added. If psutil was already imported by a prior test,
        this assertion is vacuously satisfied for that key — the important invariant is
        that the detection module load itself does not pull in the heavy path.

        Assumption: the detection module is named sentinel.detection.
        """
        before = frozenset(sys.modules.keys())
        import sentinel.detection  # noqa: F401 — check side-effects of this import

        after = frozenset(sys.modules.keys())
        new_modules = after - before
        assert "psutil" not in new_modules, (
            "sentinel.detection imported psutil at module-load time; "
            "real OS adapters must use deferred/lazy imports inside functions"
        )

    def test_when_detection_module_imported_then_no_subprocess_is_spawned_at_load_time(
        self,
    ):
        """
        AC3: importing sentinel.detection must not spawn any subprocess.

        Uses subprocess.Popen as a sentinel: if build_detection or any top-level
        code in the module calls subprocess, the patched version raises and the
        test fails immediately.

        Assumption: the module is not already in sys.modules (or was freshly imported
        before this point). Since the previous test in this class also imports it, this
        test primarily guards against code paths triggered at re-import or at object
        construction time.
        """
        build_detection = _build_detection()
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("subprocess spawned at construction"),
        ):
            with patch(
                "subprocess.run",
                side_effect=AssertionError("subprocess.run called at construction"),
            ):
                pipeline = build_detection(MonitorConfig())
        assert isinstance(pipeline, _pipeline_cls())

    def test_when_build_detection_called_without_fakes_then_pipeline_is_returned(self):
        """
        AC3: build_detection(config) with default (deferred) adapters returns DetectionPipeline.

        Construction must succeed without triggering any OS calls — deferred imports
        guarantee the heavy paths are only hit when detect() is actually called.
        """
        build_detection = _build_detection()
        pipeline = build_detection(MonitorConfig())
        assert isinstance(pipeline, _pipeline_cls())


# ═══════════════════════════════════════════════════════════════════════════════
#  AC4 — build_detection(config, detectors=<fakes>) accepts injected fakes,
#         mirroring build_pipeline(config, readers=...)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFakeInjection:
    def test_when_fake_process_detector_injected_then_it_is_called_by_detect(self):
        """AC4: injected process detector is the one invoked — not a real OS adapter."""
        proc = _make_process_candidate(pid=42, name="Firefox")
        build_detection = _build_detection()
        proc_det = _CallCountingProcessDetector(candidates=(proc,))
        cont_det = _CallCountingContainerDetector(candidates=())
        pipeline = build_detection(
            MonitorConfig(),
            detectors={"process": proc_det, "container": cont_det},
        )
        result = pipeline.detect(SentinelState.WARN)
        assert any(c.info.pid == 42 for c in result.processes)

    def test_when_fake_container_detector_injected_then_it_is_called_by_detect(self):
        """AC4: injected container detector is the one invoked — not a real Docker adapter."""
        cont = _make_container_candidate("my_test_service")
        build_detection = _build_detection()
        proc_det = _CallCountingProcessDetector(candidates=())
        cont_det = _CallCountingContainerDetector(candidates=(cont,))
        pipeline = build_detection(
            MonitorConfig(),
            detectors={"process": proc_det, "container": cont_det},
        )
        result = pipeline.detect(SentinelState.WARN)
        assert any(c.name == "my_test_service" for c in result.containers)

    def test_when_both_fakes_injected_then_detect_delegates_only_to_them(self):
        """AC4: with full fake injection, neither real OS adapter path executes."""
        build_detection = _build_detection()
        proc_det = _CallCountingProcessDetector(candidates=())
        cont_det = _CallCountingContainerDetector(candidates=())
        pipeline = build_detection(
            MonitorConfig(),
            detectors={"process": proc_det, "container": cont_det},
        )
        pipeline.detect(SentinelState.WARN)
        assert proc_det.call_count == 1
        assert cont_det.call_count == 1

    def test_when_fakes_injected_then_construction_accepts_any_valid_config(self):
        """AC4: the injection interface works regardless of MonitorConfig field values."""
        build_detection = _build_detection()
        config = MonitorConfig(confirm_samples=1, confirm_samples_clear=1, cooldown=0.0)
        proc_det = _CallCountingProcessDetector(candidates=())
        cont_det = _CallCountingContainerDetector(candidates=())
        pipeline = build_detection(
            config,
            detectors={"process": proc_det, "container": cont_det},
        )
        assert isinstance(pipeline, _pipeline_cls())


# ═══════════════════════════════════════════════════════════════════════════════
#  AC5 — end-to-end integration with scripted fakes
#         NORMAL → empty; WARN/CRITICAL/DISK_LOW → candidates present
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndToEndIntegration:
    """
    Full integration walk driven entirely by scripted fakes.
    No real OS, Docker daemon, or subprocess is invoked.
    """

    def _pipeline_with_candidates(
        self,
    ) -> tuple[object, _CallCountingProcessDetector, _CallCountingContainerDetector]:
        proc = _make_process_candidate(pid=1, name="Chrome")
        cont = _make_container_candidate("clipcraft_api")
        return _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )

    def test_when_normal_then_result_has_no_processes(self):
        """AC5 NORMAL leg: process list is empty."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert pipeline.detect(SentinelState.NORMAL).processes == ()

    def test_when_normal_then_result_has_no_containers(self):
        """AC5 NORMAL leg: container list is empty."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert pipeline.detect(SentinelState.NORMAL).containers == ()

    def test_when_warn_then_process_candidates_present(self):
        """AC5 WARN leg: at least one process candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.WARN).processes) >= 1

    def test_when_warn_then_container_candidates_present(self):
        """AC5 WARN leg: at least one container candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.WARN).containers) >= 1

    def test_when_critical_then_process_candidates_present(self):
        """AC5 CRITICAL leg: at least one process candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.CRITICAL).processes) >= 1

    def test_when_critical_then_container_candidates_present(self):
        """AC5 CRITICAL leg: at least one container candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.CRITICAL).containers) >= 1

    def test_when_disk_low_then_process_candidates_present(self):
        """AC5 DISK_LOW leg: at least one process candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.DISK_LOW).processes) >= 1

    def test_when_disk_low_then_container_candidates_present(self):
        """AC5 DISK_LOW leg: at least one container candidate returned."""
        pipeline, _, _ = self._pipeline_with_candidates()
        assert len(pipeline.detect(SentinelState.DISK_LOW).containers) >= 1

    def test_full_state_walk_produces_expected_emptiness_and_presence(self):
        """AC5 combined: NORMAL→empty; WARN/CRITICAL/DISK_LOW→candidates present."""
        pipeline, _, _ = self._pipeline_with_candidates()

        normal = pipeline.detect(SentinelState.NORMAL)
        warn = pipeline.detect(SentinelState.WARN)
        critical = pipeline.detect(SentinelState.CRITICAL)
        disk_low = pipeline.detect(SentinelState.DISK_LOW)

        # NORMAL — empty on both axes
        assert normal.processes == () and normal.containers == ()

        # All non-NORMAL states — candidates on both axes
        for label, result in [
            ("WARN", warn),
            ("CRITICAL", critical),
            ("DISK_LOW", disk_low),
        ]:
            assert len(result.processes) >= 1, f"{label}: expected process candidates"
            assert len(result.containers) >= 1, (
                f"{label}: expected container candidates"
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  AC6 — no mutating call (kill/stop/delete) ever issued across the run
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutatingCallsAcrossRun:
    """
    AC6: DetectionPipeline.detect() is a pure read/classify pass.

    The fakes expose kill(), stop(), and delete() spy methods that record any
    invocations. After running a full NORMAL→WARN→CRITICAL→DISK_LOW walk, all
    spy lists must be empty.
    """

    def test_when_full_walk_run_then_no_kill_is_ever_called(self):
        """AC6: kill() is never called on the process detector during any detection pass."""
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, proc_det, _ = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        for state in [
            SentinelState.NORMAL,
            SentinelState.WARN,
            SentinelState.CRITICAL,
            SentinelState.DISK_LOW,
        ]:
            pipeline.detect(state)

        assert proc_det.kill_calls == [], (
            f"Unexpected kill() calls during detection: {proc_det.kill_calls}"
        )

    def test_when_full_walk_run_then_no_stop_is_ever_called(self):
        """AC6: stop() is never called on the container detector during any detection pass."""
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, _, cont_det = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        for state in [
            SentinelState.NORMAL,
            SentinelState.WARN,
            SentinelState.CRITICAL,
            SentinelState.DISK_LOW,
        ]:
            pipeline.detect(state)

        assert cont_det.stop_calls == [], (
            f"Unexpected stop() calls during detection: {cont_det.stop_calls}"
        )

    def test_when_full_walk_run_then_no_delete_is_ever_called(self):
        """AC6: delete() is never called on the container detector during any detection pass."""
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, _, cont_det = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        for state in [
            SentinelState.NORMAL,
            SentinelState.WARN,
            SentinelState.CRITICAL,
            SentinelState.DISK_LOW,
        ]:
            pipeline.detect(state)

        assert cont_det.delete_calls == [], (
            f"Unexpected delete() calls during detection: {cont_det.delete_calls}"
        )

    def test_when_warn_state_run_then_no_mutating_call_on_any_fake(self):
        """AC6 spot-check: a single WARN run issues no kill/stop/delete on either fake."""
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, proc_det, cont_det = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        pipeline.detect(SentinelState.WARN)

        assert proc_det.kill_calls == []
        assert cont_det.stop_calls == []
        assert cont_det.delete_calls == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Property-based tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalStateIdempotenceInvariant:
    @hyp_settings(max_examples=50)
    @given(n=st.integers(min_value=1, max_value=20))
    def test_when_detect_normal_called_n_times_then_always_returns_empty(self, n: int):
        """
        Idempotence invariant: detect(NORMAL) is always empty regardless of call count.

        Derived from criterion: detect(NORMAL) → empty DetectionResult and performs
        no OS calls. Calling it N times must never change the result.
        """
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, _, _ = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        for _ in range(n):
            result = pipeline.detect(SentinelState.NORMAL)
            assert result.processes == ()
            assert result.containers == ()


class TestDetectNeverRaisesForAnyValidState:
    @hyp_settings(max_examples=20)
    @given(state=st.sampled_from(list(SentinelState)))
    def test_when_detect_called_with_any_sentinel_state_then_no_error_is_raised(
        self, state: SentinelState
    ) -> None:
        """
        Never-raises invariant: detect() is total over all SentinelState values.

        Derived from criterion: DetectionPipeline.detect(state) -> DetectionResult
        for all possible states.
        """
        proc = _make_process_candidate()
        cont = _make_container_candidate()
        pipeline, _, _ = _make_pipeline(
            process_candidates=(proc,),
            container_candidates=(cont,),
        )
        result = pipeline.detect(state)
        assert isinstance(result, DetectionResult)


class TestNormalStateNeverDelegatesForAnyInput:
    @hyp_settings(max_examples=50)
    @given(
        proc_count=st.integers(min_value=0, max_value=5),
        cont_count=st.integers(min_value=0, max_value=5),
    )
    def test_when_normal_then_no_detector_is_ever_called_regardless_of_candidates(
        self, proc_count: int, cont_count: int
    ) -> None:
        """
        Never-delegates invariant: detect(NORMAL) never calls either inner detector,
        regardless of how many candidates the fakes are scripted to return.

        Derived from criterion: detect(NORMAL) performs no OS calls.
        """
        proc_candidates = tuple(
            _make_process_candidate(pid=i, name=f"App{i}")
            for i in range(1, proc_count + 1)
        )
        cont_candidates = tuple(
            _make_container_candidate(f"svc_{i}") for i in range(cont_count)
        )
        pipeline, proc_det, cont_det = _make_pipeline(
            process_candidates=proc_candidates,
            container_candidates=cont_candidates,
        )
        pipeline.detect(SentinelState.NORMAL)
        assert proc_det.call_count == 0
        assert cont_det.call_count == 0
