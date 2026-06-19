"""
Source-blind example tests for Issue #36:
  LaunchctlServiceController — install/uninstall/start/stop/status
  (gui/$UID domain, no sudo, injected runner, tmp LaunchAgents dir)

All tests are derived from the acceptance criteria and requirements.md only.
No implementation source was read.

Assumptions recorded for the implementer
─────────────────────────────────────────
• LaunchctlServiceController lives at:
    sentinel.service.controller.LaunchctlServiceController
• ServiceController protocol lives at:
    sentinel.domain.protocols.ServiceController
  ServiceController must be decorated @runtime_checkable (required for the
  isinstance test below; explicit subclassing also satisfies it).
• Constructor signature:
    LaunchctlServiceController(
        config,               # has .label (str) and .exit_timeout (int)
        paths,                # has .log_dir (Path)
        launch_agents_dir: Path,   # default ~/Library/LaunchAgents; injectable for tests
        runner: Callable,     # injectable subprocess runner, called as
                              #   runner(args: list[str], **kwargs) → CompletedProcess
                              #   raises CalledProcessError on failure
    )
• install()  → renders plist via sentinel.service.plist.render_plist (issue #35),
               writes it to <launch_agents_dir>/<label>.plist,
               then calls: launchctl bootstrap gui/<UID> <plist_path>
               Fallback on CalledProcessError: launchctl load <plist_path>
               ProgramArguments must include the resolved python_executable (sys.executable
               or equivalent) so the service can be restarted by launchd.
• uninstall() → launchctl bootout gui/<UID>/<label>
               Fallback on CalledProcessError: launchctl unload <plist_path>
               Then unlinks the plist file (FileNotFoundError → silently ignored).
• start()    → launchctl kickstart gui/<UID>/<label>
• stop()     → launchctl kill <SIGNAL> gui/<UID>/<label>
• status()   → launchctl print gui/<UID>/<label> → parsed into non-empty human string;
               returns an error-description string on failure (never raises).
• All methods catch CalledProcessError and report a clear message — never propagate
  into the CLI caller.
"""

import os
import plistlib
import subprocess
import tempfile
from pathlib import Path

from hypothesis import given, strategies as st

# Red-phase imports — will fail until the implementation exists.
from sentinel.domain.protocols import ServiceController
from sentinel.service.controller import LaunchctlServiceController

# ─────────────────────────────────────────────────────────────────────────────
# Constants derived from the acceptance criteria
# ─────────────────────────────────────────────────────────────────────────────

LABEL = "com.sentinel.test"
UID = os.getuid()
GUI_DOMAIN = f"gui/{UID}"

_LAUNCHCTL_PRINT_RUNNING = f"""\
{{
    label = {LABEL}
    state = running
    pid = 42
    last exit code = 0
}}
"""

_LAUNCHCTL_PRINT_STOPPED = f"""\
{{
    label = {LABEL}
    state = not running
    last exit code = 0
}}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Test doubles — built from the criteria; no src/ reading
# ─────────────────────────────────────────────────────────────────────────────


class _Config:
    """Minimal config double: .label + .exit_timeout (matching the #35 plist contract)."""

    def __init__(self, label: str = LABEL, exit_timeout: int = 20):
        self.label = label
        self.exit_timeout = exit_timeout


class _Paths:
    """
    Minimal paths double with .log_dir.
    requirements.md: logs live under ~/Library/Application Support/Sentinel/
    """

    def __init__(self, base: Path):
        self._log_dir = base / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def log_dir(self) -> Path:
        return self._log_dir


class _FakeRunner:
    """
    Captures every argv list passed to it.

    fail_on  — set of launchctl subcommand names that should raise CalledProcessError,
               simulating a partial launchd failure (e.g. bootstrap unavailable).
    output_map — maps subcommand name → stdout string returned in CompletedProcess.
    """

    def __init__(
        self,
        fail_on: frozenset = frozenset(),
        output_map: dict | None = None,
    ):
        self.invocations: list[list[str]] = []
        self._fail_on = fail_on
        self._output_map = output_map or {}

    def __call__(self, args, **kwargs):
        args = list(args)
        self.invocations.append(args)
        subcmd = args[1] if len(args) > 1 else args[0]
        if subcmd in self._fail_on:
            raise subprocess.CalledProcessError(
                1, args, stderr="simulated launchd failure"
            )
        stdout = self._output_map.get(subcmd, "")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


class _AlwaysFailRunner:
    """Simulates a completely unavailable launchd — every call raises CalledProcessError."""

    def __call__(self, args, **kwargs):
        raise subprocess.CalledProcessError(1, list(args), stderr="launchd unavailable")


# ─────────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────────────


def _la_dir(tmp_path: Path) -> Path:
    d = tmp_path / "LaunchAgents"
    d.mkdir(exist_ok=True)
    return d


def _make_controller(
    tmp_path: Path,
    runner=None,
    label: str = LABEL,
) -> LaunchctlServiceController:
    """Build a fully-injected controller from criteria-derived test doubles."""
    return LaunchctlServiceController(
        config=_Config(label=label),
        paths=_Paths(tmp_path),
        launch_agents_dir=_la_dir(tmp_path),
        runner=runner or _FakeRunner(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC 1 — LaunchctlServiceController implements ServiceController
#         (install / uninstall / start / stop / status)
# ─────────────────────────────────────────────────────────────────────────────


def test_when_controller_is_instantiated_then_it_exposes_all_service_controller_methods(
    tmp_path,
):
    ctrl = _make_controller(tmp_path)
    for method_name in ("install", "uninstall", "start", "stop", "status"):
        assert callable(getattr(ctrl, method_name, None)), (
            f"LaunchctlServiceController must have a callable .{method_name}() method"
        )


def test_when_controller_is_instantiated_then_it_is_an_instance_of_service_controller(
    tmp_path,
):
    """Requires ServiceController to be @runtime_checkable (or explicit subclassing)."""
    ctrl = _make_controller(tmp_path)
    assert isinstance(ctrl, ServiceController)


# ─────────────────────────────────────────────────────────────────────────────
# AC 2 — install(): resolves python_executable, writes plist,
#         calls 'launchctl bootstrap gui/$UID <plist>' (fallback: load)
# ─────────────────────────────────────────────────────────────────────────────


def test_when_install_is_called_then_plist_is_written_under_launch_agents_dir(tmp_path):
    """install() must write <label>.plist into the injected LaunchAgents directory."""
    ctrl = _make_controller(tmp_path)
    ctrl.install()
    assert (_la_dir(tmp_path) / f"{LABEL}.plist").exists(), (
        f"install() must create {_la_dir(tmp_path)}/{LABEL}.plist"
    )


def test_when_install_is_called_then_written_plist_is_parseable_by_plistlib(tmp_path):
    """The plist written by install() must be valid Apple plist XML."""
    ctrl = _make_controller(tmp_path)
    ctrl.install()
    data = plistlib.loads((_la_dir(tmp_path) / f"{LABEL}.plist").read_bytes())
    assert isinstance(data, dict), "Installed plist must deserialise to a dict"


def test_when_install_is_called_then_plist_program_arguments_includes_python_executable(
    tmp_path,
):
    """install() must resolve python_executable and embed it in ProgramArguments."""
    ctrl = _make_controller(tmp_path)
    ctrl.install()
    data = plistlib.loads((_la_dir(tmp_path) / f"{LABEL}.plist").read_bytes())
    args = data.get("ProgramArguments", [])
    assert len(args) >= 1, "ProgramArguments must be non-empty"
    assert any("python" in a.lower() or "sentinel" in a.lower() for a in args), (
        "ProgramArguments must include the python executable or sentinel entrypoint; "
        f"got: {args}"
    )


def test_when_install_is_called_then_launchctl_bootstrap_uses_gui_uid_domain(tmp_path):
    """install() must call 'launchctl bootstrap gui/<UID> <plist_path>'."""
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.install()
    plist_path = str(_la_dir(tmp_path) / f"{LABEL}.plist")
    assert any(
        len(cmd) >= 4
        and cmd[:3] == ["launchctl", "bootstrap", GUI_DOMAIN]
        and plist_path in cmd
        for cmd in runner.invocations
    ), (
        f"Expected 'launchctl bootstrap {GUI_DOMAIN} {plist_path}'; "
        f"invocations: {runner.invocations}"
    )


def test_when_bootstrap_fails_then_install_falls_back_to_launchctl_load(tmp_path):
    """install() must fall back to 'launchctl load <plist>' when bootstrap raises."""
    runner = _FakeRunner(fail_on=frozenset({"bootstrap"}))
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.install()
    assert any(cmd[1] == "load" for cmd in runner.invocations), (
        "install() must fall back to 'launchctl load' when bootstrap raises CalledProcessError"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC 3 — uninstall(): 'bootout gui/$UID/<label>' (fallback: unload) then unlink
# ─────────────────────────────────────────────────────────────────────────────


def test_when_uninstall_is_called_then_launchctl_bootout_uses_gui_uid_label(tmp_path):
    """uninstall() must call 'launchctl bootout gui/<UID>/<label>'."""
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.uninstall()
    expected_target = f"{GUI_DOMAIN}/{LABEL}"
    assert any(
        cmd[:3] == ["launchctl", "bootout", expected_target]
        for cmd in runner.invocations
    ), (
        f"Expected 'launchctl bootout {expected_target}'; "
        f"invocations: {runner.invocations}"
    )


def test_when_bootout_fails_then_uninstall_falls_back_to_launchctl_unload(tmp_path):
    """uninstall() must fall back to 'launchctl unload <plist>' when bootout raises."""
    la = _la_dir(tmp_path)
    (la / f"{LABEL}.plist").write_text("placeholder")
    runner = _FakeRunner(fail_on=frozenset({"bootout"}))
    ctrl = LaunchctlServiceController(
        config=_Config(),
        paths=_Paths(tmp_path),
        launch_agents_dir=la,
        runner=runner,
    )
    ctrl.uninstall()
    assert any(cmd[1] == "unload" for cmd in runner.invocations), (
        "uninstall() must fall back to 'launchctl unload' when bootout raises CalledProcessError"
    )


def test_when_uninstall_is_called_then_plist_file_is_removed(tmp_path):
    """uninstall() must unlink the plist file after running bootout/unload."""
    la = _la_dir(tmp_path)
    plist = la / f"{LABEL}.plist"
    plist.write_text("placeholder")
    ctrl = LaunchctlServiceController(
        config=_Config(),
        paths=_Paths(tmp_path),
        launch_agents_dir=la,
        runner=_FakeRunner(),
    )
    ctrl.uninstall()
    assert not plist.exists(), "uninstall() must delete the plist file"


# ─────────────────────────────────────────────────────────────────────────────
# AC 4 — start()/stop() via kickstart/kill; status() parses launchctl print
# ─────────────────────────────────────────────────────────────────────────────


def test_when_start_is_called_then_launchctl_kickstart_uses_gui_uid_label(tmp_path):
    """start() must call 'launchctl kickstart gui/<UID>/<label>'."""
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.start()
    expected_target = f"{GUI_DOMAIN}/{LABEL}"
    assert any(
        cmd[1] == "kickstart" and expected_target in cmd for cmd in runner.invocations
    ), (
        f"Expected 'launchctl kickstart ... {expected_target}'; "
        f"invocations: {runner.invocations}"
    )


def test_when_stop_is_called_then_launchctl_kill_uses_gui_uid_label(tmp_path):
    """stop() must call 'launchctl kill <SIGNAL> gui/<UID>/<label>'."""
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.stop()
    expected_target = f"{GUI_DOMAIN}/{LABEL}"
    assert any(
        cmd[1] == "kill" and expected_target in cmd for cmd in runner.invocations
    ), (
        f"Expected 'launchctl kill ... {expected_target}'; "
        f"invocations: {runner.invocations}"
    )


def test_when_status_is_called_then_launchctl_print_uses_gui_uid_label(tmp_path):
    """status() must call 'launchctl print gui/<UID>/<label>'."""
    runner = _FakeRunner(output_map={"print": _LAUNCHCTL_PRINT_RUNNING})
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.status()
    expected_target = f"{GUI_DOMAIN}/{LABEL}"
    assert any(
        cmd[1] == "print" and expected_target in cmd for cmd in runner.invocations
    ), (
        f"Expected 'launchctl print {expected_target}'; "
        f"invocations: {runner.invocations}"
    )


def test_when_status_is_called_then_it_returns_a_non_empty_human_string(tmp_path):
    """status() must parse launchctl print output into a non-empty human-readable string."""
    runner = _FakeRunner(output_map={"print": _LAUNCHCTL_PRINT_RUNNING})
    ctrl = _make_controller(tmp_path, runner=runner)
    result = ctrl.status()
    assert isinstance(result, str) and result.strip(), (
        "status() must return a non-empty string summarising the service state"
    )


def test_when_launchctl_print_shows_running_state_then_status_mentions_running(
    tmp_path,
):
    """
    status() parsing must surface the 'running' state from launchctl print output.

    Assumption: when launchctl print contains 'state = running', the returned
    human string includes the word 'running' (case-insensitive).
    """
    runner = _FakeRunner(output_map={"print": _LAUNCHCTL_PRINT_RUNNING})
    ctrl = _make_controller(tmp_path, runner=runner)
    result = ctrl.status()
    assert "running" in result.lower(), (
        f"status() should mention 'running' when launchctl print shows state=running; "
        f"got: {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC 5 — Every launchctl call uses gui/$UID; no sudo; no system/ domain
# ─────────────────────────────────────────────────────────────────────────────


def test_when_install_is_called_then_no_launchctl_invocation_uses_sudo(tmp_path):
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.install()
    for cmd in runner.invocations:
        assert "sudo" not in cmd, (
            f"launchctl must not use sudo; install() issued: {cmd}"
        )


def test_when_install_is_called_then_no_launchctl_invocation_uses_system_domain(
    tmp_path,
):
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.install()
    for cmd in runner.invocations:
        assert not any("system/" in arg for arg in cmd), (
            f"launchctl must not use system/ domain; install() issued: {cmd}"
        )


def test_when_uninstall_is_called_then_no_launchctl_invocation_uses_sudo(tmp_path):
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.uninstall()
    for cmd in runner.invocations:
        assert "sudo" not in cmd, (
            f"launchctl must not use sudo; uninstall() issued: {cmd}"
        )


def test_when_start_stop_status_are_called_then_no_launchctl_invocation_uses_sudo(
    tmp_path,
):
    runner = _FakeRunner(output_map={"print": _LAUNCHCTL_PRINT_RUNNING})
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.start()
    ctrl.stop()
    ctrl.status()
    for cmd in runner.invocations:
        assert "sudo" not in cmd, f"launchctl must not use sudo; found: {cmd}"


def test_when_start_stop_status_are_called_then_no_launchctl_invocation_uses_system_domain(
    tmp_path,
):
    runner = _FakeRunner(output_map={"print": _LAUNCHCTL_PRINT_RUNNING})
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.start()
    ctrl.stop()
    ctrl.status()
    for cmd in runner.invocations:
        assert not any("system/" in arg for arg in cmd), (
            f"launchctl must not use system/ domain; found: {cmd}"
        )


def test_when_install_is_called_then_all_launchctl_invocations_contain_gui_uid_domain(
    tmp_path,
):
    """Every launchctl call must reference gui/<UID>, never a different domain."""
    runner = _FakeRunner()
    ctrl = _make_controller(tmp_path, runner=runner)
    ctrl.install()
    for cmd in runner.invocations:
        if cmd[0] == "launchctl" and len(cmd) > 2:
            assert any(GUI_DOMAIN in arg for arg in cmd[2:]), (
                f"Every launchctl call must use the gui/{UID} domain; found: {cmd}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# AC 6 — Fail safe: never raise into the CLI when launchd is unavailable
# ─────────────────────────────────────────────────────────────────────────────


def test_when_launchd_is_unavailable_then_install_does_not_raise(tmp_path):
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    ctrl.install()  # must not raise


def test_when_launchd_is_unavailable_then_uninstall_does_not_raise(tmp_path):
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    ctrl.uninstall()  # must not raise


def test_when_launchd_is_unavailable_then_start_does_not_raise(tmp_path):
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    ctrl.start()  # must not raise


def test_when_launchd_is_unavailable_then_stop_does_not_raise(tmp_path):
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    ctrl.stop()  # must not raise


def test_when_launchd_is_unavailable_then_status_does_not_raise(tmp_path):
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    ctrl.status()  # must not raise


def test_when_launchd_is_unavailable_then_status_returns_a_non_empty_error_message(
    tmp_path,
):
    """status() must still return an informative string when launchctl fails."""
    ctrl = _make_controller(tmp_path, runner=_AlwaysFailRunner())
    result = ctrl.status()
    assert isinstance(result, str) and result.strip(), (
        "status() must return a non-empty error message when launchd is unavailable"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property-based tests (Hypothesis)
# ─────────────────────────────────────────────────────────────────────────────


# Invariant (never-raises-for-valid-input):
#   status() is a total function over the domain of launchctl print output strings.
#   For ANY string output (even malformed), it must return a non-empty string
#   without raising. The criterion says "parses … into a short human string" —
#   that parsing must be robust to arbitrary input.
@given(st.text())
def test_when_launchctl_print_returns_any_output_then_status_always_returns_a_string(
    output,
):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        la = base / "LaunchAgents"
        la.mkdir()
        runner = _FakeRunner(output_map={"print": output})
        ctrl = LaunchctlServiceController(
            config=_Config(),
            paths=_Paths(base),
            launch_agents_dir=la,
            runner=runner,
        )
        result = ctrl.status()
        assert isinstance(result, str) and len(result) > 0, (
            f"status() must return non-empty string for any launchctl print output; "
            f"got {result!r} for input {output!r}"
        )


# Invariant (structural / domain guarantee):
#   For ANY non-empty label string, start() must embed gui/<UID>/<label> in the
#   kickstart command. The gui/$UID domain rule is not label-specific — it applies
#   universally across all valid labels.
@given(
    st.text(
        min_size=1,
        max_size=80,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters=".-",
        ),
    )
)
def test_when_start_is_called_with_any_label_then_kickstart_always_uses_gui_uid_domain(
    label,
):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        la = base / "LaunchAgents"
        la.mkdir()
        runner = _FakeRunner()
        ctrl = LaunchctlServiceController(
            config=_Config(label=label),
            paths=_Paths(base),
            launch_agents_dir=la,
            runner=runner,
        )
        ctrl.start()
        expected = f"{GUI_DOMAIN}/{label}"
        kickstart_calls = [
            cmd for cmd in runner.invocations if len(cmd) > 1 and cmd[1] == "kickstart"
        ]
        assert len(kickstart_calls) >= 1, (
            f"start() must invoke launchctl kickstart for label={label!r}"
        )
        assert any(expected in cmd for cmd in kickstart_calls), (
            f"kickstart must reference '{expected}'; got: {kickstart_calls}"
        )
