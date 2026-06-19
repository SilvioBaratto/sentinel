"""
Source-blind example tests for DockerContainerStopper — Issue #22.

Every test is derived from the issue's acceptance criteria only.
No implementation source was read during authoring (Red-phase TDD).

Skipped per oracle:
  - AC-5: docker callable raising (oracle: NOT VERIFIABLE)
  - SOLID / test-suite-gate criteria (oracle: NOT VERIFIABLE)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from unittest.mock import MagicMock

from hypothesis import given
from hypothesis import strategies as st

from sentinel.domain.protocols import ContainerStopper
from sentinel.domain.value_objects import ActionKind, Reversibility
from sentinel.execute.docker_stopper import DockerContainerStopper


# ---------------------------------------------------------------------------
# Minimal test-double — satisfies the "candidate" interface implied by
# "command is exactly ['stop', name]": candidate must expose .name
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    name: str


def _c(name: str) -> _Candidate:
    return _Candidate(name=name)


# ---------------------------------------------------------------------------
# AC-1  DockerContainerStopper implements ContainerStopper
# ---------------------------------------------------------------------------


class TestDockerContainerStopperProtocol:
    def test_when_instantiated_then_it_satisfies_container_stopper_protocol(self):
        stopper = DockerContainerStopper(docker=MagicMock())
        assert isinstance(stopper, ContainerStopper)


# ---------------------------------------------------------------------------
# AC-2  Always-up containers refused: zero docker calls, success=False,
#        reversibility=REVERSIBLE, non-empty detail
# ---------------------------------------------------------------------------


class TestAlwaysUpContainersRefusedOptimizerPrefix:
    def test_when_name_starts_with_optimizer_then_zero_docker_calls(self):
        spy = MagicMock()
        DockerContainerStopper(docker=spy).stop(_c("optimizer_frontend"))
        assert spy.call_count == 0

    def test_when_name_starts_with_optimizer_then_success_is_false(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(
            _c("optimizer_frontend")
        )
        assert result.success is False

    def test_when_name_starts_with_optimizer_then_reversibility_is_reversible(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(_c("optimizer_api"))
        assert result.reversibility == Reversibility.REVERSIBLE

    def test_when_name_starts_with_optimizer_then_detail_explains_refusal(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(_c("optimizer_api"))
        assert isinstance(result.detail, str)
        assert len(result.detail) > 0


class TestAlwaysUpContainersRefusedDbSuffix:
    def test_when_name_ends_with_db_then_zero_docker_calls(self):
        spy = MagicMock()
        DockerContainerStopper(docker=spy).stop(_c("optimizer_db"))
        assert spy.call_count == 0

    def test_when_name_ends_with_db_then_success_is_false(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(_c("postgres_db"))
        assert result.success is False

    def test_when_name_ends_with_db_then_reversibility_is_reversible(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(_c("postgres_db"))
        assert result.reversibility == Reversibility.REVERSIBLE

    def test_when_name_ends_with_db_then_detail_explains_refusal(self):
        result = DockerContainerStopper(docker=MagicMock()).stop(_c("postgres_db"))
        assert isinstance(result.detail, str)
        assert len(result.detail) > 0


# ---------------------------------------------------------------------------
# AC-3  Eligible candidates — docker callable invoked with exactly
#        ["stop", name]; forbidden substrings never appear in any arg
# ---------------------------------------------------------------------------


class TestEligibleCandidateCommandShape:
    def test_when_eligible_container_stopped_then_command_is_stop_name(self):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_frontend"))
        assert captured == [["stop", "clipcraft_frontend"]]

    def test_when_eligible_container_stopped_then_no_rm_in_any_captured_arg(self):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_api"))
        for cmd in captured:
            assert "rm" not in cmd

    def test_when_eligible_container_stopped_then_no_volumes_flag_in_any_captured_arg(
        self,
    ):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_api"))
        for cmd in captured:
            assert "--volumes" not in cmd

    def test_when_eligible_container_stopped_then_no_minus_v_in_any_captured_arg(self):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_api"))
        for cmd in captured:
            assert "-v" not in cmd

    def test_when_eligible_container_stopped_then_no_volume_word_in_any_captured_arg(
        self,
    ):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_api"))
        for cmd in captured:
            assert "volume" not in cmd

    def test_when_eligible_container_stopped_then_no_prune_in_any_captured_arg(self):
        captured: List[List[str]] = []

        def spy(cmd: List[str]) -> None:
            captured.append(list(cmd))

        DockerContainerStopper(docker=spy).stop(_c("clipcraft_api"))
        for cmd in captured:
            assert "prune" not in cmd


# ---------------------------------------------------------------------------
# AC-4  Successful stop → kind=STOP_CONTAINER, reversibility=REVERSIBLE,
#        success=True, bytes_freed=0
# ---------------------------------------------------------------------------


class TestSuccessfulStopResultShape:
    def test_when_eligible_container_stopped_then_success_is_true(self):
        result = DockerContainerStopper(docker=lambda cmd: None).stop(
            _c("clipcraft_frontend")
        )
        assert result.success is True

    def test_when_eligible_container_stopped_then_kind_is_stop_container(self):
        result = DockerContainerStopper(docker=lambda cmd: None).stop(
            _c("clipcraft_frontend")
        )
        assert result.kind == ActionKind.STOP_CONTAINER

    def test_when_eligible_container_stopped_then_reversibility_is_reversible(self):
        result = DockerContainerStopper(docker=lambda cmd: None).stop(
            _c("clipcraft_frontend")
        )
        assert result.reversibility == Reversibility.REVERSIBLE

    def test_when_eligible_container_stopped_then_bytes_freed_is_zero(self):
        result = DockerContainerStopper(docker=lambda cmd: None).stop(
            _c("clipcraft_frontend")
        )
        assert result.bytes_freed == 0


# ---------------------------------------------------------------------------
# AC-5  docker callable raising → success=False, no exception escapes
# ---------------------------------------------------------------------------


class TestDockerCallableRaisingIsHandled:
    def test_when_docker_callable_raises_then_success_is_false(self):
        def exploding_docker(cmd: list) -> None:
            raise RuntimeError("daemon down")

        result = DockerContainerStopper(docker=exploding_docker).stop(
            _c("clipcraft_frontend")
        )
        assert result.success is False

    def test_when_docker_callable_raises_then_no_exception_escapes(self):
        def exploding_docker(cmd: list) -> None:
            raise RuntimeError("daemon down")

        # must not raise — result is returned, not exception propagated
        DockerContainerStopper(docker=exploding_docker).stop(_c("clipcraft_api"))


# ---------------------------------------------------------------------------
# AC-6  Property (hypothesis): any optimizer_* / *_db name is refused with
#        zero docker calls — invariant must hold across all valid names
# ---------------------------------------------------------------------------

_NAME_PART = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=30,
)


@given(suffix=_NAME_PART)
def test_when_name_matches_optimizer_prefix_then_zero_docker_calls_for_all_suffixes(
    suffix: str,
) -> None:
    spy = MagicMock()
    DockerContainerStopper(docker=spy).stop(_c(f"optimizer_{suffix}"))
    assert spy.call_count == 0


@given(suffix=_NAME_PART)
def test_when_name_matches_optimizer_prefix_then_always_refused_for_all_suffixes(
    suffix: str,
) -> None:
    result = DockerContainerStopper(docker=MagicMock()).stop(_c(f"optimizer_{suffix}"))
    assert result.success is False


@given(prefix=_NAME_PART)
def test_when_name_matches_db_suffix_then_zero_docker_calls_for_all_prefixes(
    prefix: str,
) -> None:
    spy = MagicMock()
    DockerContainerStopper(docker=spy).stop(_c(f"{prefix}_db"))
    assert spy.call_count == 0


@given(prefix=_NAME_PART)
def test_when_name_matches_db_suffix_then_always_refused_for_all_prefixes(
    prefix: str,
) -> None:
    result = DockerContainerStopper(docker=MagicMock()).stop(_c(f"{prefix}_db"))
    assert result.success is False
