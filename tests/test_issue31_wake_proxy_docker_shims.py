"""Tests for issue #31: wake-proxy Docker shims — port discovery + safe stack restarter.

Source-blind spec tests updated for implementation:
- Injected callable parameter name is `docker` (matches existing adapter pattern).
- DockerStackRestarter.restart() accepts WakeRegistration (matches StackRestarter protocol);
  the restart_command encodes the compose-vs-start decision made at stop time.
- Safety guard uses exact token equality — container names that happen to contain
  forbidden substrings (e.g. "alarm_service" contains "rm") must NOT be rejected.
- HostConfig.PortBindings is parsed as fallback when NetworkSettings.Ports is empty
  (i.e. when the container is stopped).
"""

from __future__ import annotations

import json
from typing import Callable

import pytest
from hypothesis import given, settings, strategies as st

from sentinel.domain.value_objects import (
    PublishedPort,
    StackPorts,
    WakeOutcome,
    WakeRegistration,
)
from sentinel.docker.port_discovery import DockerPortDiscoverer
from sentinel.docker.stack_restarter import DockerStackRestarter


# ---------------------------------------------------------------------------
# Shared safety-guard helper (AC-4) — exact equality, never substring
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {"rm", "--volumes", "-v", "volume", "prune"}
)


def _assert_safe_docker_args(args: list[str]) -> None:
    """Reject any docker argv whose element exactly matches a forbidden token."""
    for token in args:
        assert token not in _FORBIDDEN_TOKENS, (
            f"Forbidden docker token {token!r} found in command: {args}"
        )


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_INSPECT_JSON: str = json.dumps(
    [
        {
            "Name": "/clipcraft_api",
            "NetworkSettings": {
                "Ports": {
                    "3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3001"}],
                    "3001/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3002"}],
                }
            },
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "clipcraft",
                    "com.docker.compose.service": "api",
                }
            },
        }
    ]
)

_INSPECT_NO_COMPOSE_LABELS: str = json.dumps(
    [
        {
            "Name": "/standalone",
            "NetworkSettings": {
                "Ports": {
                    "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
                }
            },
            "Config": {"Labels": {}},
        }
    ]
)

# Stopped container: NetworkSettings.Ports is empty, HostConfig.PortBindings has the binding
_INSPECT_STOPPED_WITH_HOST_CONFIG: str = json.dumps(
    [
        {
            "Name": "/clipcraft_api",
            "NetworkSettings": {"Ports": {}},
            "HostConfig": {
                "PortBindings": {
                    "3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3001"}],
                }
            },
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "clipcraft",
                    "com.docker.compose.service": "api",
                }
            },
        }
    ]
)


def _make_runner(
    return_value: str = "",
    raises: Exception | None = None,
) -> tuple[Callable[[list[str]], str], list[list[str]]]:
    """Return (runner_callable, calls_log)."""
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(list(args))
        if raises is not None:
            raise raises  # noqa: TRY301
        return return_value

    return runner, calls


def _compose_registration(
    stack: str = "clipcraft_api",
    project: str = "clipcraft",
) -> WakeRegistration:
    return WakeRegistration(
        stack=stack,
        ports=(PublishedPort(host_ip="0.0.0.0", host_port=3001, container_port=3000),),
        restart_command=("compose", "-p", project, "up", "-d"),
    )


def _standalone_registration(stack: str = "standalone") -> WakeRegistration:
    return WakeRegistration(
        stack=stack,
        ports=(PublishedPort(host_ip="0.0.0.0", host_port=8080, container_port=80),),
        restart_command=("start", stack),
    )


# ===========================================================================
# AC-1  DockerPortDiscoverer.discover — happy path
# ===========================================================================


def test_when_inspect_returns_valid_json_then_result_is_stack_ports():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    assert isinstance(result, StackPorts)


def test_when_inspect_returns_valid_json_then_stack_field_equals_requested_name():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    assert result.stack == "clipcraft_api"


def test_when_inspect_returns_valid_json_then_ports_are_non_empty():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    assert len(result.ports) > 0


def test_when_inspect_returns_valid_json_then_each_port_is_published_port_instance():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    for port in result.ports:
        assert isinstance(port, PublishedPort)


def test_when_inspect_returns_valid_json_then_host_port_is_extracted_from_network_settings():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    host_ports = {p.host_port for p in result.ports}
    assert 3001 in host_ports


def test_when_inspect_returns_valid_json_with_multiple_ports_then_all_published_ports_returned():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    host_ports = {p.host_port for p in result.ports}
    assert 3001 in host_ports
    assert 3002 in host_ports


def test_when_inspect_returns_compose_labels_then_compose_project_is_extracted():
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    assert result.compose_project == "clipcraft"


def test_when_inspect_returns_no_compose_labels_then_compose_project_is_none():
    runner, _ = _make_runner(return_value=_INSPECT_NO_COMPOSE_LABELS)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("standalone")

    assert result.compose_project is None


# ===========================================================================
# AC-1 (extended) HostConfig.PortBindings fallback for stopped containers
# ===========================================================================


def test_when_network_settings_ports_empty_then_host_config_port_bindings_are_used():
    """Stopped containers have empty NetworkSettings.Ports; HostConfig.PortBindings persists."""
    runner, _ = _make_runner(return_value=_INSPECT_STOPPED_WITH_HOST_CONFIG)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    host_ports = {p.host_port for p in result.ports}
    assert 3001 in host_ports


def test_when_network_settings_ports_empty_then_result_ports_are_non_empty():
    runner, _ = _make_runner(return_value=_INSPECT_STOPPED_WITH_HOST_CONFIG)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("clipcraft_api")

    assert len(result.ports) > 0


def test_when_network_settings_ports_populated_then_host_config_is_not_used_as_primary():
    """NetworkSettings.Ports takes precedence when it has bindings (running container)."""
    both_sources = json.dumps(
        [
            {
                "Name": "/svc",
                "NetworkSettings": {
                    "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9090"}]}
                },
                "HostConfig": {
                    "PortBindings": {
                        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "7070"}]
                    }
                },
                "Config": {"Labels": {}},
            }
        ]
    )
    runner, _ = _make_runner(return_value=both_sources)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("svc")

    host_ports = {p.host_port for p in result.ports}
    assert 9090 in host_ports  # NetworkSettings value
    assert (
        7070 not in host_ports
    )  # HostConfig value NOT used when NetworkSettings has data


# ===========================================================================
# AC-2  Malformed / empty / exception → safe default; never raises
# ===========================================================================


def test_when_inspect_returns_empty_json_array_then_safe_default_is_returned():
    runner, _ = _make_runner(return_value="[]")
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("mycontainer")

    assert result == StackPorts(
        stack="mycontainer", containers=("mycontainer",), ports=()
    )


def test_when_inspect_returns_empty_string_then_safe_default_is_returned():
    runner, _ = _make_runner(return_value="")
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("mycontainer")

    assert result == StackPorts(
        stack="mycontainer", containers=("mycontainer",), ports=()
    )


def test_when_inspect_returns_malformed_json_then_safe_default_is_returned():
    runner, _ = _make_runner(return_value="not json {{{")
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("mycontainer")

    assert result == StackPorts(
        stack="mycontainer", containers=("mycontainer",), ports=()
    )


def test_when_inspect_raises_then_safe_default_is_returned():
    runner, _ = _make_runner(raises=RuntimeError("docker not found"))
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("mycontainer")

    assert result == StackPorts(
        stack="mycontainer", containers=("mycontainer",), ports=()
    )


def test_when_inspect_raises_os_error_then_no_exception_propagates():
    runner, _ = _make_runner(raises=OSError("permission denied"))
    discoverer = DockerPortDiscoverer(docker=runner)

    discoverer.discover("mycontainer")  # must not raise


def test_when_inspect_returns_json_without_network_settings_then_safe_default_is_returned():
    broken = json.dumps([{"Name": "/foo", "Config": {"Labels": {}}}])
    runner, _ = _make_runner(return_value=broken)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("foo")

    assert result == StackPorts(stack="foo", containers=("foo",), ports=())


def test_when_inspect_returns_json_with_no_host_binding_then_port_is_excluded():
    """Ports exposed but not published (null binding) must not appear in result."""
    unbound = json.dumps(
        [
            {
                "Name": "/foo",
                "NetworkSettings": {"Ports": {"8080/tcp": None}},
                "Config": {"Labels": {}},
            }
        ]
    )
    runner, _ = _make_runner(return_value=unbound)
    discoverer = DockerPortDiscoverer(docker=runner)

    result = discoverer.discover("foo")

    assert result.ports == ()


# ===========================================================================
# AC-3  DockerStackRestarter.restart + is_running
# ===========================================================================


def test_when_compose_restart_command_given_then_docker_compose_up_dash_d_is_called():
    runner, calls = _make_runner()
    restarter = DockerStackRestarter(docker=runner)

    restarter.restart(_compose_registration())

    assert any(
        "-p" in c and "clipcraft" in c and "up" in c and "-d" in c for c in calls
    ), f"Expected compose up call; got: {calls}"


def test_when_start_restart_command_given_then_docker_start_is_called():
    runner, calls = _make_runner()
    restarter = DockerStackRestarter(docker=runner)

    restarter.restart(_standalone_registration(stack="standalone"))

    assert any("start" in c and "standalone" in c for c in calls), (
        f"Expected docker start call; got: {calls}"
    )


def test_when_is_running_is_called_then_docker_ps_command_is_used():
    runner, calls = _make_runner(return_value="")
    restarter = DockerStackRestarter(docker=runner)

    restarter.is_running("clipcraft_api")

    assert any("ps" in c for c in calls), f"Expected docker ps; got: {calls}"


def test_when_container_name_is_in_docker_ps_output_then_is_running_returns_true():
    def runner(args: list[str]) -> str:
        if "ps" in args:
            return "clipcraft_api   myimage   Up 2 hours\n"
        return ""

    assert DockerStackRestarter(docker=runner).is_running("clipcraft_api") is True


def test_when_container_name_is_absent_from_docker_ps_output_then_is_running_returns_false():
    def runner(args: list[str]) -> str:
        if "ps" in args:
            return "other_container   myimage   Up 5 minutes\n"
        return ""

    assert DockerStackRestarter(docker=runner).is_running("clipcraft_api") is False


def test_when_docker_ps_returns_empty_string_then_is_running_returns_false():
    runner, _ = _make_runner(return_value="")
    assert DockerStackRestarter(docker=runner).is_running("anything") is False


# ===========================================================================
# AC-4  Safety guard — exact equality, no false positives on substrings
# ===========================================================================


def test_when_args_contain_rm_token_then_assert_safe_docker_args_raises():
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(["docker", "rm", "mycontainer"])


def test_when_args_contain_volumes_flag_then_assert_safe_docker_args_raises():
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(["docker", "rm", "--volumes", "mycontainer"])


def test_when_args_contain_dash_v_then_assert_safe_docker_args_raises():
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(["docker", "stop", "-v", "mycontainer"])


def test_when_args_contain_prune_then_assert_safe_docker_args_raises():
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(["docker", "system", "prune"])


def test_when_args_contain_volume_subcommand_then_assert_safe_docker_args_raises():
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(["docker", "volume", "ls"])


def test_when_container_name_contains_rm_as_substring_then_safety_guard_does_not_reject():
    """'alarm_service' contains 'rm' as substring — must NOT be rejected (exact match only)."""
    _assert_safe_docker_args(["start", "alarm_service"])


def test_when_container_name_contains_volume_as_substring_then_safety_guard_does_not_reject():
    """'myvolume_db' contains 'volume' as substring — must NOT be rejected."""
    _assert_safe_docker_args(["start", "myvolume_db"])


def test_when_args_are_safe_compose_up_then_assert_safe_docker_args_does_not_raise():
    _assert_safe_docker_args(["compose", "-p", "clipcraft", "up", "-d"])


def test_when_args_are_safe_docker_start_then_assert_safe_docker_args_does_not_raise():
    _assert_safe_docker_args(["start", "mycontainer"])


def test_when_args_are_safe_docker_ps_then_assert_safe_docker_args_does_not_raise():
    _assert_safe_docker_args(["ps", "--format", "{{.Names}}"])


def test_when_discoverer_calls_docker_inspect_then_no_forbidden_tokens_are_used():
    """DockerPortDiscoverer must never issue a destructive docker command."""

    def tracking_runner(args: list[str]) -> str:
        _assert_safe_docker_args(args)
        return _VALID_INSPECT_JSON

    DockerPortDiscoverer(docker=tracking_runner).discover("clipcraft_api")


def test_when_restarter_restarts_compose_stack_then_no_forbidden_tokens_are_used():
    """DockerStackRestarter must never issue a destructive docker command."""

    def tracking_runner(args: list[str]) -> str:
        _assert_safe_docker_args(args)
        return ""

    DockerStackRestarter(docker=tracking_runner).restart(_compose_registration())


def test_when_restarter_restarts_standalone_container_then_no_forbidden_tokens_are_used():
    def tracking_runner(args: list[str]) -> str:
        _assert_safe_docker_args(args)
        return ""

    DockerStackRestarter(docker=tracking_runner).restart(_standalone_registration())


def test_when_is_running_is_called_then_no_forbidden_tokens_are_used():
    def tracking_runner(args: list[str]) -> str:
        _assert_safe_docker_args(args)
        return ""

    DockerStackRestarter(docker=tracking_runner).is_running("clipcraft_api")


# ===========================================================================
# AC-5  Failure / already-running outcomes; never raises
# ===========================================================================


def test_when_restart_runner_raises_runtime_error_then_wake_outcome_restart_failed_is_returned():
    runner, _ = _make_runner(raises=RuntimeError("docker daemon unreachable"))
    restarter = DockerStackRestarter(docker=runner)

    result = restarter.restart(_compose_registration())

    assert result == WakeOutcome.RESTART_FAILED


def test_when_restart_runner_raises_os_error_then_wake_outcome_restart_failed_is_returned():
    runner, _ = _make_runner(raises=OSError("no such file"))
    restarter = DockerStackRestarter(docker=runner)

    result = restarter.restart(_standalone_registration())

    assert result == WakeOutcome.RESTART_FAILED


def test_when_restart_runner_raises_then_no_exception_propagates():
    runner, _ = _make_runner(raises=Exception("unexpected"))
    restarter = DockerStackRestarter(docker=runner)

    restarter.restart(_compose_registration())  # must not raise


def test_when_container_already_running_then_restart_returns_already_running():
    def runner(args: list[str]) -> str:
        if "ps" in args:
            return "clipcraft_api   myimage   Up 3 hours\n"
        return ""

    result = DockerStackRestarter(docker=runner).restart(_compose_registration())

    assert result == WakeOutcome.ALREADY_RUNNING


def test_when_container_already_running_then_no_restart_command_is_issued():
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(list(args))
        if "ps" in args:
            return "clipcraft_api   myimage   Up 3 hours\n"
        return ""

    DockerStackRestarter(docker=runner).restart(_compose_registration())

    assert not any("up" in c or "start" in c for c in calls), (
        f"No restart command expected when already running; got: {calls}"
    )


# ===========================================================================
# Property-based tests (Hypothesis) — invariants derived from criteria
# ===========================================================================


@given(st.text(min_size=1))
def test_when_inspect_raises_for_any_name_then_safe_default_is_always_returned(
    name: str,
):
    """AC-2 invariant: for ANY non-empty name, a runner exception → safe StackPorts."""
    runner, _ = _make_runner(raises=RuntimeError("boom"))
    result = DockerPortDiscoverer(docker=runner).discover(name)
    assert result == StackPorts(stack=name, containers=(name,), ports=())


@given(st.text(min_size=1))
def test_when_inspect_returns_malformed_for_any_name_then_safe_default_is_always_returned(
    name: str,
) -> None:
    """AC-2 invariant: for ANY non-empty name, invalid JSON → safe StackPorts."""
    runner, _ = _make_runner(return_value="not json {{{")
    result = DockerPortDiscoverer(docker=runner).discover(name)
    assert result == StackPorts(stack=name, containers=(name,), ports=())


@given(st.text(min_size=1))
@settings(max_examples=50)
def test_when_discover_succeeds_then_stack_field_always_equals_requested_name(
    name: str,
) -> None:
    """AC-1 invariant: returned StackPorts.stack always equals the argument to discover()."""
    runner, _ = _make_runner(return_value=_VALID_INSPECT_JSON)
    result = DockerPortDiscoverer(docker=runner).discover(name)
    assert result.stack == name


@given(st.lists(st.sampled_from(sorted(_FORBIDDEN_TOKENS)), min_size=1))
def test_when_args_contain_any_forbidden_token_then_helper_always_rejects(
    forbidden_tokens: list[str],
) -> None:
    """AC-4 invariant: _assert_safe_docker_args rejects any list with a forbidden token."""
    args = ["docker", "compose"] + forbidden_tokens
    with pytest.raises(AssertionError):
        _assert_safe_docker_args(args)
