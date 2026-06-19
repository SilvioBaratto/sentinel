"""
Source-blind tests for Issue #39 — Typer CLI + console script + packaging.

Acceptance criteria covered (runtime-verifiable only):
  [UNIT] install/uninstall/start/stop delegate to ServiceController and report a clear result
  [UNIT] sentinel status prints pressure level/usage, recent actions with reversibility,
         idle candidates, and audit-log tail (formatted via fmt.format_bytes)
  [UNIT] Hidden sentinel run builds and runs SentinelDaemon (launchd entrypoint)
  [UNIT] [project.scripts] sentinel = "sentinel.cli:app" added; typer in [project] dependencies
  [UNIT] All tests pass via Typer CliRunner + injected fakes; each command hits the right seam

Skipped (NOT VERIFIABLE per oracle):
  - Commands load config through config store and never crash on missing config (defaults)
  - SOLID, clean code / TDD (subjective prose)

Seam contract (implementation must provide these injectable factories):
  sentinel.cli._build_controller()         → ServiceController
  sentinel.cli._build_daemon()             → SentinelDaemon
  sentinel.cli._build_status_reporter()   → status data object

  Tests patch these factory callables to inject fakes and assert delegation.

format_bytes location assumed: sentinel.fmt.format_bytes — derived from "formatted via
fmt.format_bytes" in the criterion text.
"""

from __future__ import annotations

import re
import tomllib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Fakes — derived entirely from the acceptance criteria, not from src/
# ---------------------------------------------------------------------------


class FakeController:
    """Records which lifecycle methods were called and returns confirmation strings."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self) -> str:
        self.calls.append("install")
        return "Installed"

    def uninstall(self) -> str:
        self.calls.append("uninstall")
        return "Uninstalled"

    def start(self) -> str:
        self.calls.append("start")
        return "Started"

    def stop(self) -> str:
        self.calls.append("stop")
        return "Stopped"


class FakeDaemon:
    """Records whether run() was invoked (the launchd entrypoint)."""

    def __init__(self) -> None:
        self.ran = False

    def run(self) -> None:
        self.ran = True


class FakeStatusReporter:
    """
    Returns canned status data for assertion.

    The four data attributes map directly to the four output sections the
    criterion mandates sentinel status prints:
      pressure_level / pressure_label  → current pressure level/usage
      recent_actions                   → recent actions with reversibility
      idle_candidates                  → idle candidates list
      audit_log_tail                   → audit-log tail (bytes formatted via format_bytes)

    swap_used_bytes is included so the status formatter can exercise format_bytes;
    design decision: the CLI calls format_bytes on at least this value.
    """

    def __init__(self) -> None:
        self.pressure_level: int = 2
        self.pressure_label: str = "WARN"
        # 512 MB in bytes — used to verify format_bytes is called (not raw integer)
        self.swap_used_bytes: int = 512 * 1024 * 1024  # 536_870_912
        self.disk_free_bytes: int = 25 * 1024 * 1024 * 1024
        self.recent_actions: list[dict] = [
            {
                "description": "Closed Chrome",
                "reversible": True,
                "bytes": 200 * 1024 * 1024,
            },
            {"description": "Stopped clipcraft_api", "reversible": False, "bytes": 0},
        ]
        self.idle_candidates: list[str] = ["Safari", "Discord"]
        self.audit_log_tail: list[str] = [
            "[2026-06-19 00:00:01] Closed Chrome — reversible",
            "[2026-06-19 00:00:02] Stopped clipcraft_api — permanent",
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUNNER = CliRunner()
_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _invoke(
    args: list[str],
    *,
    controller: FakeController | None = None,
    daemon: FakeDaemon | None = None,
    reporter: FakeStatusReporter | None = None,
):
    """
    Invoke the Typer app via CliRunner with optional fake dependencies injected
    through the module-level factory seams.

    Lazy-imports sentinel.cli inside the patch context so patches are active
    before any command runs.
    """
    with ExitStack() as stack:
        if controller is not None:
            stack.enter_context(
                patch("sentinel.cli._build_controller", return_value=controller)
            )
        if daemon is not None:
            stack.enter_context(
                patch("sentinel.cli._build_daemon", return_value=daemon)
            )
        if reporter is not None:
            stack.enter_context(
                patch("sentinel.cli._build_status_reporter", return_value=reporter)
            )
        from sentinel.cli import app  # noqa: PLC0415

        return _RUNNER.invoke(app, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Criterion 1 — install/uninstall/start/stop delegate to ServiceController
# ---------------------------------------------------------------------------


def test_when_install_is_invoked_then_controller_install_is_called():
    ctrl = FakeController()
    _invoke(["install"], controller=ctrl)
    assert "install" in ctrl.calls


def test_when_install_is_invoked_then_a_clear_result_is_printed():
    ctrl = FakeController()
    result = _invoke(["install"], controller=ctrl)
    assert result.exit_code == 0
    assert result.output.strip()


def test_when_uninstall_is_invoked_then_controller_uninstall_is_called():
    ctrl = FakeController()
    _invoke(["uninstall"], controller=ctrl)
    assert "uninstall" in ctrl.calls


def test_when_uninstall_is_invoked_then_a_clear_result_is_printed():
    ctrl = FakeController()
    result = _invoke(["uninstall"], controller=ctrl)
    assert result.exit_code == 0
    assert result.output.strip()


def test_when_start_is_invoked_then_controller_start_is_called():
    ctrl = FakeController()
    _invoke(["start"], controller=ctrl)
    assert "start" in ctrl.calls


def test_when_start_is_invoked_then_a_clear_result_is_printed():
    ctrl = FakeController()
    result = _invoke(["start"], controller=ctrl)
    assert result.exit_code == 0
    assert result.output.strip()


def test_when_stop_is_invoked_then_controller_stop_is_called():
    ctrl = FakeController()
    _invoke(["stop"], controller=ctrl)
    assert "stop" in ctrl.calls


def test_when_stop_is_invoked_then_a_clear_result_is_printed():
    ctrl = FakeController()
    result = _invoke(["stop"], controller=ctrl)
    assert result.exit_code == 0
    assert result.output.strip()


def test_when_install_is_invoked_then_only_install_is_called_on_controller():
    ctrl = FakeController()
    _invoke(["install"], controller=ctrl)
    assert ctrl.calls == ["install"]


def test_when_stop_is_invoked_then_only_stop_is_called_on_controller():
    ctrl = FakeController()
    _invoke(["stop"], controller=ctrl)
    assert ctrl.calls == ["stop"]


# ---------------------------------------------------------------------------
# Criterion 2 — sentinel status output
# ---------------------------------------------------------------------------


def test_when_status_is_invoked_then_pressure_level_appears_in_output():
    result = _invoke(["status"], reporter=FakeStatusReporter())
    output = result.output
    # The criterion mandates the current pressure level is printed.
    # Level 2 == WARN; either the integer or the label must appear.
    assert "2" in output or "WARN" in output.upper()


def test_when_status_is_invoked_then_recent_action_description_appears_in_output():
    result = _invoke(["status"], reporter=FakeStatusReporter())
    # At least one recent action description from the fake must be visible.
    assert "Chrome" in result.output or "clipcraft" in result.output


def test_when_status_is_invoked_then_reversibility_is_indicated_in_output():
    """
    The criterion says 'recent actions with reversibility'.
    Either the word 'reversible' or 'permanent' must appear to signal reversibility.
    """
    result = _invoke(["status"], reporter=FakeStatusReporter())
    lower = result.output.lower()
    assert "revers" in lower or "permanent" in lower


def test_when_status_is_invoked_then_idle_candidates_appear_in_output():
    result = _invoke(["status"], reporter=FakeStatusReporter())
    assert "Safari" in result.output or "Discord" in result.output


def test_when_status_is_invoked_then_audit_log_tail_appears_in_output():
    result = _invoke(["status"], reporter=FakeStatusReporter())
    # A timestamp from the canned audit log tail must be visible.
    assert "2026-06-19" in result.output


def test_when_status_is_invoked_then_raw_swap_byte_count_is_not_printed():
    """
    The criterion mandates formatting via fmt.format_bytes.
    If format_bytes is used, the raw integer 536870912 (512 MB) must not appear
    verbatim in the output — a human-readable form (e.g. '512.0 MB') must instead.
    """
    result = _invoke(["status"], reporter=FakeStatusReporter())
    assert "536870912" not in result.output


def test_when_status_is_invoked_then_output_contains_a_byte_unit():
    """
    A byte-unit indicator (B, KB, MB, GB) must appear in the status output,
    confirming that format_bytes produced a human-readable value.
    """
    result = _invoke(["status"], reporter=FakeStatusReporter())
    assert re.search(r"\d[\d.]*\s*(B|KB|MB|GB|TB)", result.output, re.IGNORECASE)


def test_when_status_is_invoked_then_exit_code_is_zero():
    result = _invoke(["status"], reporter=FakeStatusReporter())
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Criterion 3 — hidden sentinel run invokes SentinelDaemon.run()
# ---------------------------------------------------------------------------


def test_when_run_is_invoked_then_daemon_run_is_called():
    daemon = FakeDaemon()
    result = _invoke(["run"], daemon=daemon)
    assert result.exit_code == 0
    assert daemon.ran is True


def test_when_help_is_requested_then_run_command_is_not_listed():
    """
    The criterion says sentinel run is *hidden* (launchd entrypoint, not a user command).
    It must not appear in the --help output.
    """
    from sentinel.cli import app  # noqa: PLC0415

    result = _RUNNER.invoke(app, ["--help"])
    # 'run' must not appear as a listed command
    assert "run" not in result.output


# ---------------------------------------------------------------------------
# Criterion 4 — pyproject.toml packaging
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pyproject_data() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())


def test_when_pyproject_toml_is_read_then_sentinel_script_entry_is_declared(
    pyproject_data: dict,
):
    scripts: dict[str, str] = pyproject_data.get("project", {}).get("scripts", {})
    assert "sentinel" in scripts, "[project.scripts] sentinel entry is missing"


def test_when_pyproject_toml_is_read_then_sentinel_script_points_to_sentinel_cli_app(
    pyproject_data: dict,
):
    entry = pyproject_data["project"]["scripts"]["sentinel"]
    assert entry == "sentinel.cli:app"


def test_when_pyproject_toml_is_read_then_typer_is_in_project_dependencies(
    pyproject_data: dict,
):
    deps: list[str] = pyproject_data.get("project", {}).get("dependencies", [])
    assert any(dep.lower().startswith("typer") for dep in deps), (
        "typer not found in [project] dependencies"
    )


# ---------------------------------------------------------------------------
# Criterion 6 — CliRunner smoke: all lifecycle commands exit cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["install", "uninstall", "start", "stop"])
def test_when_lifecycle_command_is_invoked_then_exit_code_is_zero(cmd: str):
    ctrl = FakeController()
    result = _invoke([cmd], controller=ctrl)
    assert result.exit_code == 0


@pytest.mark.parametrize("cmd", ["install", "uninstall", "start", "stop"])
def test_when_lifecycle_command_is_invoked_then_exactly_one_controller_method_is_called(
    cmd: str,
):
    """Each command must delegate to exactly one controller method (its own)."""
    ctrl = FakeController()
    _invoke([cmd], controller=ctrl)
    assert ctrl.calls == [cmd]


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
#
# Criterion: status output is formatted via fmt.format_bytes.
# Invariant (never-raises-for-valid-input): format_bytes must handle any
# non-negative integer in a realistic byte-count range without raising.
# Invariant (output structure): the returned string must always contain a
# numeric value followed by a byte-unit label.
# ---------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=10**15))
@hyp_settings(max_examples=200)
def test_when_any_valid_byte_count_is_passed_to_format_bytes_then_no_error_is_raised(
    count: int,
) -> None:
    """
    Derived from: 'formatted via fmt.format_bytes'.
    Invariant: format_bytes is a total function over non-negative integers up to
    a petabyte — any value in this domain must not raise.
    """
    from sentinel import fmt  # noqa: PLC0415

    fmt.format_bytes(count)  # must not raise


@given(st.integers(min_value=0, max_value=10**15))
@hyp_settings(max_examples=200)
def test_when_any_valid_byte_count_is_formatted_then_result_contains_a_unit_label(
    count: int,
) -> None:
    """
    Invariant: format_bytes always returns a human-readable string that includes
    a byte-unit suffix (B, KB, MB, GB, or TB).
    """
    from sentinel import fmt  # noqa: PLC0415

    result = fmt.format_bytes(count)
    assert isinstance(result, str), "format_bytes must return a str"
    assert re.search(r"(B|KB|MB|GB|TB)$", result.strip(), re.IGNORECASE), (
        f"format_bytes({count!r}) returned {result!r} — no unit suffix found"
    )
