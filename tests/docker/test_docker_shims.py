"""
Source-blind example tests for Issue #15 — docker OS shims.

Authored from acceptance criteria only (Red phase). Every test will fail until
the corresponding implementation is written.

Canned-output convention:
  Every reader accepts a `docker` callable with signature:
      Callable[[list[str]], str]
  which wraps a subprocess call.  Tests inject a pure-Python stub; no real
  Docker daemon is touched.

Byte-unit convention derived from the criterion text (SI decimal):
  B  → 1
  kB → 1 000
  MB → 1 000 000
  GB → 1 000 000 000
"""

import json
from typing import Callable

import pytest
from hypothesis import given, strategies as st

from sentinel.docker.stats_reader import DockerStatsReader
from sentinel.docker.byte_parser import parse_bytes, parse_byte_pair
from sentinel.docker.allow_list import ContainerAllowList
from sentinel.docker.session_reader import DockerSessionReader
from sentinel.domain.value_objects import ContainerStats


# ── helpers ──────────────────────────────────────────────────────────────────


def _docker_returning(output: str) -> Callable[[list[str]], str]:
    """Return a docker stub that always yields *output* regardless of args."""

    def _docker(args: list[str]) -> str:
        return output

    return _docker


def _docker_raising(args: list[str]) -> str:
    """Simulate daemon-down: any call raises OSError."""
    raise OSError("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")


# ── canned docker stats data ─────────────────────────────────────────────────

_STATS_ONE = json.dumps(
    {
        "ID": "abc123def456",
        "Name": "optimizer_frontend",
        "CPUPerc": "0.50%",
        "NetIO": "1.2kB / 3.4MB",
        "BlockIO": "5GB / 2MB",
    }
)

_STATS_TWO = json.dumps(
    {
        "ID": "def456abc123",
        "Name": "clipcraft_api",
        "CPUPerc": "1.00%",
        "NetIO": "0B / 0B",
        "BlockIO": "0B / 0B",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# DockerStatsReader
# ─────────────────────────────────────────────────────────────────────────────


class TestDockerStatsReaderShape:
    def test_when_single_stats_line_is_given_then_one_element_is_returned(self):
        reader = DockerStatsReader(docker=_docker_returning(_STATS_ONE))
        assert len(reader.read()) == 1

    def test_when_read_is_called_then_result_is_a_tuple(self):
        reader = DockerStatsReader(docker=_docker_returning(_STATS_ONE))
        assert isinstance(reader.read(), tuple)

    def test_when_multiple_stats_lines_are_given_then_all_containers_are_returned(self):
        output = "\n".join([_STATS_ONE, _STATS_TWO])
        reader = DockerStatsReader(docker=_docker_returning(output))
        result = reader.read()
        assert len(result) == 2
        assert {s.name for s in result} == {"optimizer_frontend", "clipcraft_api"}

    def test_when_stats_line_is_parsed_then_each_element_is_a_container_stats(self):
        reader = DockerStatsReader(docker=_docker_returning(_STATS_ONE))
        assert isinstance(reader.read()[0], ContainerStats)


class TestDockerStatsReaderFields:
    def _stat(self) -> ContainerStats:
        return DockerStatsReader(docker=_docker_returning(_STATS_ONE)).read()[0]

    def test_when_stats_line_is_parsed_then_id_is_correct(self):
        assert self._stat().container_id == "abc123def456"

    def test_when_stats_line_is_parsed_then_name_is_correct(self):
        assert self._stat().name == "optimizer_frontend"

    def test_when_stats_line_is_parsed_then_cpu_percent_is_correct(self):
        assert self._stat().cpu_percent == pytest.approx(0.50)

    def test_when_stats_line_is_parsed_then_net_rx_bytes_is_correct(self):
        assert self._stat().net_rx_bytes == 1_200

    def test_when_stats_line_is_parsed_then_net_tx_bytes_is_correct(self):
        assert self._stat().net_tx_bytes == 3_400_000

    def test_when_stats_line_is_parsed_then_block_read_bytes_is_correct(self):
        assert self._stat().block_read_bytes == 5_000_000_000

    def test_when_stats_line_is_parsed_then_block_write_bytes_is_correct(self):
        assert self._stat().block_write_bytes == 2_000_000

    def test_when_net_io_is_zero_slash_zero_then_both_bytes_fields_are_zero(self):
        stat = DockerStatsReader(docker=_docker_returning(_STATS_TWO)).read()[0]
        assert stat.net_rx_bytes == 0
        assert stat.net_tx_bytes == 0


class TestDockerStatsReaderFailSafe:
    def test_when_output_is_empty_then_empty_tuple_is_returned(self):
        reader = DockerStatsReader(docker=_docker_returning(""))
        assert reader.read() == ()

    def test_when_docker_daemon_is_down_then_empty_tuple_is_returned_not_raised(self):
        reader = DockerStatsReader(docker=_docker_raising)
        assert reader.read() == ()

    def test_when_output_is_not_json_then_empty_tuple_is_returned_not_raised(self):
        reader = DockerStatsReader(docker=_docker_returning("NOT JSON AT ALL !!@#$"))
        assert reader.read() == ()

    def test_when_output_is_json_but_missing_fields_then_empty_tuple_is_returned(self):
        bad_line = json.dumps({"unexpected": "shape"})
        reader = DockerStatsReader(docker=_docker_returning(bad_line))
        assert reader.read() == ()


# ─────────────────────────────────────────────────────────────────────────────
# Byte parser
# ─────────────────────────────────────────────────────────────────────────────


class TestParseBytes:
    def test_when_zero_bytes_string_is_parsed_then_zero_is_returned(self):
        assert parse_bytes("0B") == 0

    def test_when_kilobytes_string_is_parsed_then_correct_byte_count_is_returned(self):
        assert parse_bytes("1.2kB") == 1_200

    def test_when_megabytes_string_is_parsed_then_correct_byte_count_is_returned(self):
        assert parse_bytes("3.4MB") == 3_400_000

    def test_when_gigabytes_string_is_parsed_then_correct_byte_count_is_returned(self):
        assert parse_bytes("5GB") == 5_000_000_000

    def test_when_whole_number_bytes_is_parsed_then_integer_is_returned(self):
        assert parse_bytes("42B") == 42

    def test_when_result_is_returned_then_it_is_an_integer(self):
        assert isinstance(parse_bytes("1.2kB"), int)


class TestParseBytesPair:
    def test_when_slash_separated_pair_is_parsed_then_rx_is_first_value(self):
        rx, _ = parse_byte_pair("1.2kB / 3.4MB")
        assert rx == 1_200

    def test_when_slash_separated_pair_is_parsed_then_tx_is_second_value(self):
        _, tx = parse_byte_pair("1.2kB / 3.4MB")
        assert tx == 3_400_000

    def test_when_zero_slash_zero_pair_is_parsed_then_both_values_are_zero(self):
        rx, tx = parse_byte_pair("0B / 0B")
        assert rx == 0
        assert tx == 0

    def test_when_pair_is_parsed_then_result_is_a_two_element_tuple(self):
        result = parse_byte_pair("1.2kB / 3.4MB")
        assert isinstance(result, tuple) and len(result) == 2


# Property: any non-negative float with a valid unit never raises.
_VALID_UNITS = st.sampled_from(["B", "kB", "MB", "GB"])


@given(
    magnitude=st.floats(
        min_value=0, max_value=9999.9, allow_nan=False, allow_infinity=False
    ),
    unit=_VALID_UNITS,
)
def test_when_valid_byte_string_is_given_then_parse_bytes_does_not_raise(
    magnitude, unit
):
    parse_bytes(f"{magnitude:.1f}{unit}")


# Property: parse_byte_pair(a + " / " + b) decomposes to parse_bytes(a), parse_bytes(b).
@given(
    a_mag=st.floats(
        min_value=0, max_value=9999.9, allow_nan=False, allow_infinity=False
    ),
    a_unit=_VALID_UNITS,
    b_mag=st.floats(
        min_value=0, max_value=9999.9, allow_nan=False, allow_infinity=False
    ),
    b_unit=_VALID_UNITS,
)
def test_when_pair_string_is_parsed_then_result_matches_individual_parses(
    a_mag, a_unit, b_mag, b_unit
):
    a_str = f"{a_mag:.1f}{a_unit}"
    b_str = f"{b_mag:.1f}{b_unit}"
    rx, tx = parse_byte_pair(f"{a_str} / {b_str}")
    assert rx == parse_bytes(a_str)
    assert tx == parse_bytes(b_str)


# ─────────────────────────────────────────────────────────────────────────────
# ContainerAllowList
# ─────────────────────────────────────────────────────────────────────────────


class TestContainerAllowListAlwaysUp:
    def test_when_name_is_optimizer_frontend_then_is_always_up_is_true(self):
        assert ContainerAllowList().is_always_up("optimizer_frontend") is True

    def test_when_name_is_optimizer_api_then_is_always_up_is_true(self):
        assert ContainerAllowList().is_always_up("optimizer_api") is True

    def test_when_name_is_optimizer_db_then_is_always_up_is_true(self):
        assert ContainerAllowList().is_always_up("optimizer_db") is True

    def test_when_name_ends_with_db_suffix_then_is_always_up_is_true(self):
        assert ContainerAllowList().is_always_up("mystack_db") is True

    def test_when_name_is_clipcraft_api_then_is_always_up_is_false(self):
        assert ContainerAllowList().is_always_up("clipcraft_api") is False

    def test_when_name_has_db_only_in_the_middle_then_is_always_up_is_false(self):
        assert ContainerAllowList().is_always_up("db_backup_service") is False


class TestContainerAllowListEligible:
    def test_when_name_starts_with_clipcraft_then_is_eligible_is_true(self):
        assert ContainerAllowList().is_eligible("clipcraft_frontend") is True

    def test_when_name_is_clipcraft_api_then_is_eligible_is_true(self):
        assert ContainerAllowList().is_eligible("clipcraft_api") is True

    def test_when_name_ends_with_adminer_then_is_eligible_is_true(self):
        assert ContainerAllowList().is_eligible("clipcraft_adminer") is True

    def test_when_name_is_a_non_protected_service_then_is_eligible_is_true(self):
        assert ContainerAllowList().is_eligible("some_other_service") is True

    def test_when_name_is_optimizer_frontend_then_is_eligible_is_false(self):
        assert ContainerAllowList().is_eligible("optimizer_frontend") is False

    def test_when_name_is_optimizer_api_then_is_eligible_is_false(self):
        assert ContainerAllowList().is_eligible("optimizer_api") is False

    def test_when_name_ends_with_db_then_is_eligible_is_false(self):
        assert ContainerAllowList().is_eligible("mystack_db") is False


# Property: every name ending in _db is always-up.
@given(st.from_regex(r"[a-z][a-z0-9_]*_db", fullmatch=True))
def test_when_name_ends_with_db_then_is_always_up_is_true(name: str):
    assert ContainerAllowList().is_always_up(name) is True


# Property: is_always_up and is_eligible are mutually exclusive for any name.
@given(st.from_regex(r"[a-z][a-z0-9_]{0,30}", fullmatch=True))
def test_when_any_container_name_is_given_then_always_up_and_eligible_are_mutually_exclusive(
    name: str,
):
    al = ContainerAllowList()
    assert not (al.is_always_up(name) and al.is_eligible(name))


# ─────────────────────────────────────────────────────────────────────────────
# DockerSessionReader
# ─────────────────────────────────────────────────────────────────────────────
#
# Assumed docker callable protocol: the reader calls the docker callable with
# CLI args and receives stdout as a string.  The canned output below uses the
# JSON format produced by `docker container inspect`, where each object carries
# a "Name" (with leading "/") and an "ExecIDs" array (null when none).
# Names are expected to be returned without the leading "/".


def _inspect_docker(*containers: dict) -> Callable[[list[str]], str]:
    """Return a docker stub that emits an inspect-format JSON list."""

    def _docker(args: list[str]) -> str:
        return json.dumps(list(containers))

    return _docker


_WITH_EXEC = {"Name": "/active_container", "ExecIDs": ["exec_abc123"]}
_WITHOUT_EXEC = {"Name": "/idle_container", "ExecIDs": []}
_NULL_EXEC_IDS = {"Name": "/null_container", "ExecIDs": None}


class TestDockerSessionReaderShape:
    def test_when_active_session_names_is_called_then_result_is_a_frozenset(self):
        reader = DockerSessionReader(docker=_inspect_docker(_WITH_EXEC))
        assert isinstance(reader.active_session_names(), frozenset)

    def test_when_no_containers_have_exec_sessions_then_empty_frozenset_is_returned(
        self,
    ):
        reader = DockerSessionReader(docker=_inspect_docker(_WITHOUT_EXEC))
        assert reader.active_session_names() == frozenset()

    def test_when_output_is_empty_list_then_empty_frozenset_is_returned(self):
        reader = DockerSessionReader(docker=_docker_returning("[]"))
        assert reader.active_session_names() == frozenset()


class TestDockerSessionReaderBehavior:
    def test_when_container_has_exec_ids_then_its_name_is_in_active_set(self):
        reader = DockerSessionReader(docker=_inspect_docker(_WITH_EXEC))
        assert "active_container" in reader.active_session_names()

    def test_when_container_has_empty_exec_ids_then_its_name_is_not_in_active_set(self):
        reader = DockerSessionReader(docker=_inspect_docker(_WITHOUT_EXEC))
        assert "idle_container" not in reader.active_session_names()

    def test_when_mixed_containers_are_present_then_only_exec_names_are_returned(self):
        reader = DockerSessionReader(docker=_inspect_docker(_WITH_EXEC, _WITHOUT_EXEC))
        result = reader.active_session_names()
        assert "active_container" in result
        assert "idle_container" not in result

    def test_when_exec_ids_is_null_then_container_is_not_in_active_set(self):
        reader = DockerSessionReader(docker=_inspect_docker(_NULL_EXEC_IDS))
        assert "null_container" not in reader.active_session_names()

    def test_when_multiple_containers_have_exec_sessions_then_all_names_are_returned(
        self,
    ):
        second = {"Name": "/another_active", "ExecIDs": ["exec_xyz"]}
        reader = DockerSessionReader(docker=_inspect_docker(_WITH_EXEC, second))
        result = reader.active_session_names()
        assert "active_container" in result
        assert "another_active" in result


class TestDockerSessionReaderFailSafe:
    def test_when_docker_daemon_is_down_then_empty_frozenset_is_returned_not_raised(
        self,
    ):
        reader = DockerSessionReader(docker=_docker_raising)
        assert reader.active_session_names() == frozenset()

    def test_when_docker_returns_garbage_then_empty_frozenset_is_returned_not_raised(
        self,
    ):
        reader = DockerSessionReader(docker=_docker_returning("NOT VALID JSON {{{{"))
        assert reader.active_session_names() == frozenset()

    def test_when_docker_returns_unexpected_schema_then_empty_frozenset_is_returned(
        self,
    ):
        reader = DockerSessionReader(
            docker=_docker_returning(json.dumps([{"unexpected": True}]))
        )
        assert reader.active_session_names() == frozenset()
