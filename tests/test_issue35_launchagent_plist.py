"""
Source-blind example tests for Issue #35:
  render_plist — LaunchAgent plist generator (KeepAlive crash-only,
  Background + LowPriorityIO).

Every test is derived from the acceptance criteria text and requirements.md.
No implementation source was read.

Assumptions recorded here for the implementer
─────────────────────────────────────────────
• render_plist lives at  sentinel.service.plist.render_plist
• ServiceConfig exposes  .label (str)  and  .exit_timeout (int)
• SentinelPaths exposes  .log_dir (Path)  pointing to a directory inside
  ~/Library/Application Support/Sentinel/
  (requirements.md: "Database: none (config + rotating logs on disk under
   ~/Library/Application Support/Sentinel/)")
• The plist key for ExitTimeOut uses the int value from config.exit_timeout
"""

import builtins
import plistlib
import subprocess as _subprocess
from pathlib import Path

from hypothesis import given, strategies as st

# Red-phase import — will fail until sentinel.service.plist is implemented.
from sentinel.service.plist import render_plist


# ───────────────────────────────────────────────────────────────────────────
# Test doubles — derived from acceptance criteria, not from any src/ file
# ───────────────────────────────────────────────────────────────────────────


class _Config:
    """Minimal ServiceConfig contract from AC: needs .label and .exit_timeout."""

    def __init__(self, label: str = "com.sentinel.daemon", exit_timeout: int = 20):
        self.label = label
        self.exit_timeout = exit_timeout


class _Paths:
    """
    Minimal SentinelPaths contract from AC:
    StandardOutPath / StandardErrorPath must resolve under
    ~/Library/Application Support/Sentinel/ via this object.
    Assumption: the relevant attribute is .log_dir.
    """

    @property
    def log_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "Sentinel"


_SUPPORT_BASE = str(Path.home() / "Library" / "Application Support" / "Sentinel")
_CFG = _Config()
_PTS = _Paths()
_ARGS = ["/usr/local/bin/sentinel", "run"]


def _load(xml: str) -> dict:
    """Round-trip helper: str → plistlib.loads → dict."""
    return plistlib.loads(xml.encode())


# ───────────────────────────────────────────────────────────────────────────
# AC 1 — render_plist returns a plistlib-parseable XML plist string
# ───────────────────────────────────────────────────────────────────────────


def test_when_render_plist_called_then_result_is_a_string():
    result = render_plist(_CFG, _PTS, _ARGS)
    assert isinstance(result, str)


def test_when_render_plist_called_then_result_is_parseable_by_plistlib():
    result = render_plist(_CFG, _PTS, _ARGS)
    parsed = _load(result)  # raises ValueError/expat-error if not valid plist
    assert isinstance(parsed, dict)


# ───────────────────────────────────────────────────────────────────────────
# AC 2 — fixed plist fields
# ───────────────────────────────────────────────────────────────────────────


def test_when_render_plist_called_then_label_equals_config_label():
    cfg = _Config(label="com.example.sentinel")
    assert _load(render_plist(cfg, _PTS, _ARGS))["Label"] == "com.example.sentinel"


def test_when_render_plist_called_then_run_at_load_is_true():
    assert _load(render_plist(_CFG, _PTS, _ARGS))["RunAtLoad"] is True


def test_when_render_plist_called_then_keep_alive_is_crash_only_dict():
    """
    KeepAlive must be the dict {"Crashed": True, "SuccessfulExit": False}.
    requirements.md: "KeepAlive dict with Crashed=true (+SuccessfulExit=false)
    for crash-only restart — NOT unconditional KeepAlive=true. [3-0]"
    """
    keep_alive = _load(render_plist(_CFG, _PTS, _ARGS))["KeepAlive"]
    assert keep_alive == {"Crashed": True, "SuccessfulExit": False}


def test_when_render_plist_called_then_process_type_is_background():
    assert _load(render_plist(_CFG, _PTS, _ARGS))["ProcessType"] == "Background"


def test_when_render_plist_called_then_low_priority_io_is_true():
    assert _load(render_plist(_CFG, _PTS, _ARGS))["LowPriorityIO"] is True


def test_when_render_plist_called_then_throttle_interval_is_10():
    assert _load(render_plist(_CFG, _PTS, _ARGS))["ThrottleInterval"] == 10


# ───────────────────────────────────────────────────────────────────────────
# AC 3 — ProgramArguments and ExitTimeOut
# ───────────────────────────────────────────────────────────────────────────


def test_when_render_plist_called_then_program_arguments_equals_input_list():
    args = ["/usr/local/bin/sentinel", "run", "--config", "/etc/sentinel.toml"]
    assert _load(render_plist(_CFG, _PTS, args))["ProgramArguments"] == args


def test_when_exit_timeout_is_configured_then_plist_exit_timeout_matches():
    cfg = _Config(exit_timeout=45)
    assert _load(render_plist(cfg, _PTS, _ARGS))["ExitTimeOut"] == 45


# ───────────────────────────────────────────────────────────────────────────
# AC 4 — StandardOutPath / StandardErrorPath under Sentinel support dir
# ───────────────────────────────────────────────────────────────────────────


def test_when_render_plist_called_then_stdout_path_is_under_sentinel_support_dir():
    stdout = _load(render_plist(_CFG, _PTS, _ARGS))["StandardOutPath"]
    assert str(stdout).startswith(_SUPPORT_BASE), (
        f"StandardOutPath {stdout!r} does not start with {_SUPPORT_BASE!r}"
    )


def test_when_render_plist_called_then_stderr_path_is_under_sentinel_support_dir():
    stderr = _load(render_plist(_CFG, _PTS, _ARGS))["StandardErrorPath"]
    assert str(stderr).startswith(_SUPPORT_BASE), (
        f"StandardErrorPath {stderr!r} does not start with {_SUPPORT_BASE!r}"
    )


# ───────────────────────────────────────────────────────────────────────────
# AC 5 — pure function: no file write, no launchctl, no subprocess
# ───────────────────────────────────────────────────────────────────────────


def test_when_render_plist_called_then_no_file_is_written(monkeypatch):
    _writes: list[str] = []
    _orig_open = builtins.open

    def _guarded_open(file, mode="r", *args, **kwargs):
        if any(c in str(mode) for c in ("w", "a", "x")):
            _writes.append(str(file))
        return _orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _guarded_open)
    render_plist(_CFG, _PTS, _ARGS)
    assert _writes == [], (
        f"render_plist must be a pure function but attempted file writes: {_writes}"
    )


def test_when_render_plist_called_then_no_subprocess_is_spawned(monkeypatch):
    """launchctl and any other subprocess call must not occur (pure function)."""
    _invocations: list = []

    def _blocked(*args, **kwargs):
        _invocations.append(args)

    monkeypatch.setattr(_subprocess, "run", _blocked)
    monkeypatch.setattr(_subprocess, "call", _blocked)
    monkeypatch.setattr(_subprocess, "check_call", _blocked)
    monkeypatch.setattr(_subprocess, "check_output", _blocked)
    monkeypatch.setattr(_subprocess, "Popen", _blocked)

    render_plist(_CFG, _PTS, _ARGS)
    assert _invocations == [], (
        f"render_plist must not spawn subprocesses, but called: {_invocations}"
    )


# ───────────────────────────────────────────────────────────────────────────
# AC 6 — round-trip invariants (property-based, Hypothesis)
# ───────────────────────────────────────────────────────────────────────────


# Invariant: for *any* non-empty list of plist-safe strings as program_args,
# the rendered plist is valid XML and ProgramArguments round-trips exactly.
# XML 1.0 (used by plistlib FMT_XML) forbids surrogates ("Cs") and control
# characters ("Cc") in string values, so the valid domain excludes both.
@given(
    st.lists(
        st.text(
            min_size=1,
            alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
        ),
        min_size=1,
    )
)
def test_when_program_args_are_any_strings_then_plist_is_parseable_and_args_round_trip(
    args,
):
    result = render_plist(_Config(), _Paths(), args)
    assert _load(result)["ProgramArguments"] == args


# Invariant: ExitTimeOut in the plist always equals config.exit_timeout
# for any positive integer in a sane range.
@given(st.integers(min_value=1, max_value=3600))
def test_when_exit_timeout_is_any_positive_integer_then_plist_reflects_it(timeout):
    cfg = _Config(exit_timeout=timeout)
    result = render_plist(cfg, _Paths(), _ARGS)
    assert _load(result)["ExitTimeOut"] == timeout


# Invariant: Label in the rendered plist always equals config.label
# for any non-empty plist-safe string (surrogates and control chars excluded —
# XML 1.0 forbids both).
@given(
    st.text(
        min_size=1,
        alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    )
)
def test_when_label_is_any_non_empty_string_then_plist_label_matches(label):
    cfg = _Config(label=label)
    result = render_plist(cfg, _Paths(), _ARGS)
    assert _load(result)["Label"] == label
