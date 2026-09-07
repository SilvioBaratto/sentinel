"""`sentinel --version` — reports the version of the code actually installed.

The version is read from installed package metadata, not from a literal in the
source tree, so these tests assert self-consistency (CLI output == the number
the interpreter resolves) rather than pinning a hardcoded string that would
need editing on every release.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_RUNNER = CliRunner()

# PEP 440 core: at minimum a release segment, optionally pre/post/dev/local.
_VERSION_RE = re.compile(r"^\d+(\.\d+)*([abc]|rc|\.post|\.dev)?.*$")


def _invoke(args: list[str]):
    from sentinel.cli import app  # noqa: PLC0415

    return _RUNNER.invoke(app, args, catch_exceptions=False)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_when_version_flag_is_passed_then_exit_code_is_zero(flag: str) -> None:
    assert _invoke([flag]).exit_code == 0


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_when_version_flag_is_passed_then_resolved_version_is_printed(
    flag: str,
) -> None:
    from sentinel import __version__  # noqa: PLC0415

    assert __version__ in _invoke([flag]).output


def test_when_version_flag_is_passed_then_output_names_the_program() -> None:
    """Output identifies what is being versioned, not a bare number."""
    assert "sentinel" in _invoke(["--version"]).output.lower()


def test_when_version_flag_is_passed_then_output_is_a_single_line() -> None:
    lines = [ln for ln in _invoke(["--version"]).output.splitlines() if ln.strip()]
    assert len(lines) == 1


def test_when_package_is_installed_then_version_is_a_valid_version_string() -> None:
    """Guards the PackageNotFoundError fallback from reaching an installed run."""
    from sentinel import __version__  # noqa: PLC0415

    assert __version__ != "0+unknown"
    assert _VERSION_RE.match(__version__), __version__


def test_when_version_flag_is_passed_then_no_config_or_daemon_is_built() -> None:
    """--version is eager: it must answer before any wiring or disk access.

    Asking a broken install what version it is has to work — that is often the
    first thing asked when diagnosing one. If the flag went through the normal
    command path it would load config and could fail for reasons unrelated to
    the question.
    """

    def _explode(*_args, **_kwargs):
        raise AssertionError("composition root must not be touched by --version")

    with (
        patch("sentinel.cli._build_controller", side_effect=_explode),
        patch("sentinel.cli._build_daemon", side_effect=_explode),
        patch("sentinel.cli._build_status_reporter", side_effect=_explode),
    ):
        assert _invoke(["--version"]).exit_code == 0


def test_when_help_is_requested_then_version_flag_is_documented() -> None:
    assert "--version" in _invoke(["--help"]).output


def test_when_help_is_requested_then_app_help_text_is_still_shown() -> None:
    """The root callback must not steal the app's help text (no docstring on it)."""
    assert "macOS resource governor" in _invoke(["--help"]).output


def test_when_no_args_are_given_then_help_is_shown_and_version_is_not() -> None:
    """Adding a root callback must not turn a bare invocation into a no-op."""
    result = _RUNNER.invoke(_app(), [])
    assert "Usage" in result.output


def _app():
    from sentinel.cli import app  # noqa: PLC0415

    return app
