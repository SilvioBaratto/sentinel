"""
Source-blind example tests for Issue #20:
  kill escalation OS shims — AppleScript quitter, signal sender, alive probe.

Every test is derived directly from the acceptance-criteria text.
No file under src/ was read during authoring (Red phase of TDD).
All OS callables are injected via stubs; no real os.kill or osascript
subprocess is ever spawned.

Module path assumed: sentinel.execute.os_shims
"""

import signal as signal_module

import pytest
from hypothesis import given, strategies as st

from sentinel.execute.os_shims import (
    OsascriptAppQuitter,
    PosixAliveProbe,
    PosixProcessSignaler,
)


# ============================================================================
# OsascriptAppQuitter
# Criterion: quit(pid, name) -> bool
#   runs `osascript -e 'tell application "<name>" to quit'` via injected os_runner
#   returns True if dispatched, False on any failure (does NOT confirm exit)
# ============================================================================


class TestOsascriptAppQuitterReturnValue:
    def test_when_os_runner_succeeds_then_quit_returns_true(self):
        quitter = OsascriptAppQuitter(os_runner=lambda cmd: 0)
        assert quitter.quit(pid=1234, name="Safari") is True

    def test_when_os_runner_raises_runtime_error_then_quit_returns_false(self):
        def failing_runner(cmd):
            raise RuntimeError("osascript not found")

        quitter = OsascriptAppQuitter(os_runner=failing_runner)
        assert quitter.quit(pid=1234, name="Safari") is False

    def test_when_os_runner_raises_os_error_then_quit_returns_false(self):
        def failing_runner(cmd):
            raise OSError("subprocess failed")

        quitter = OsascriptAppQuitter(os_runner=failing_runner)
        assert quitter.quit(pid=1234, name="Safari") is False

    def test_when_os_runner_raises_base_exception_then_quit_returns_false(self):
        def failing_runner(cmd):
            raise Exception("unexpected failure")

        quitter = OsascriptAppQuitter(os_runner=failing_runner)
        assert quitter.quit(pid=1234, name="Chrome") is False


class TestOsascriptAppQuitterDoesNotConfirmExit:
    def test_when_quit_dispatched_then_os_runner_called_exactly_once(self):
        # quit() dispatches once and does NOT confirm exit (no polling loops)
        call_count = {"n": 0}

        def counting_runner(cmd):
            call_count["n"] += 1
            return 0

        quitter = OsascriptAppQuitter(os_runner=counting_runner)
        quitter.quit(pid=1234, name="Discord")
        assert call_count["n"] == 1


class TestOsascriptAppQuitterCommandSpying:
    def test_when_quit_called_then_app_name_is_embedded_in_command(self):
        captured = {}

        def spy_runner(cmd):
            captured["cmd"] = cmd
            return 0

        quitter = OsascriptAppQuitter(os_runner=spy_runner)
        quitter.quit(pid=9999, name="Google Chrome")
        assert "Google Chrome" in captured["cmd"]

    def test_when_quit_called_then_command_uses_tell_application_form(self):
        captured = {}

        def spy_runner(cmd):
            captured["cmd"] = cmd
            return 0

        quitter = OsascriptAppQuitter(os_runner=spy_runner)
        quitter.quit(pid=1234, name="Slack")
        cmd = captured["cmd"]
        assert "tell application" in cmd
        assert "to quit" in cmd

    def test_when_quit_called_then_command_includes_osascript_inline_flag(self):
        captured = {}

        def spy_runner(cmd):
            captured["cmd"] = cmd
            return 0

        quitter = OsascriptAppQuitter(os_runner=spy_runner)
        quitter.quit(pid=1234, name="Slack")
        # criterion: osascript -e '...' — the '-e' flag passes the script inline
        assert "-e" in captured["cmd"]


class TestOsascriptAppQuitterNeverRaises:
    def test_when_os_runner_raises_then_exception_never_propagates_to_caller(self):
        def exploding_runner(cmd):
            raise Exception("catastrophic failure")

        quitter = OsascriptAppQuitter(os_runner=exploding_runner)
        try:
            quitter.quit(pid=1234, name="Chrome")
        except Exception:
            pytest.fail("OsascriptAppQuitter.quit raised into its caller")


# ============================================================================
# PosixProcessSignaler
# Criterion: signal(pid, sig) -> bool
#   wraps injected os.kill
#   ProcessLookupError → False (already gone)
#   any other exception → False
# ============================================================================


class TestPosixProcessSignalerReturnValue:
    def test_when_os_kill_succeeds_then_signal_returns_true(self):
        signaler = PosixProcessSignaler(os_kill=lambda pid, sig: None)
        assert signaler.signal(pid=1234, sig=signal_module.SIGTERM) is True

    def test_when_os_kill_raises_process_lookup_error_then_signal_returns_false(self):
        def stub_kill(pid, sig):
            raise ProcessLookupError("no such process")

        signaler = PosixProcessSignaler(os_kill=stub_kill)
        assert signaler.signal(pid=9999, sig=signal_module.SIGTERM) is False

    def test_when_os_kill_raises_permission_error_then_signal_returns_false(self):
        def stub_kill(pid, sig):
            raise PermissionError("operation not permitted")

        signaler = PosixProcessSignaler(os_kill=stub_kill)
        assert signaler.signal(pid=1234, sig=signal_module.SIGTERM) is False

    def test_when_os_kill_raises_os_error_then_signal_returns_false(self):
        def stub_kill(pid, sig):
            raise OSError("generic OS error")

        signaler = PosixProcessSignaler(os_kill=stub_kill)
        assert signaler.signal(pid=1234, sig=signal_module.SIGTERM) is False

    def test_when_os_kill_raises_any_other_exception_then_signal_returns_false(self):
        def stub_kill(pid, sig):
            raise RuntimeError("unexpected runtime error")

        signaler = PosixProcessSignaler(os_kill=stub_kill)
        assert signaler.signal(pid=1234, sig=signal_module.SIGTERM) is False


class TestPosixProcessSignalerArgSpying:
    def test_when_signal_called_with_sigterm_then_os_kill_receives_sigterm(self):
        captured = {}

        def spy_kill(pid, sig):
            captured["sig"] = sig

        signaler = PosixProcessSignaler(os_kill=spy_kill)
        signaler.signal(pid=1234, sig=signal_module.SIGTERM)
        assert captured["sig"] == signal_module.SIGTERM

    def test_when_signal_called_with_sigkill_then_os_kill_receives_sigkill(self):
        captured = {}

        def spy_kill(pid, sig):
            captured["sig"] = sig

        signaler = PosixProcessSignaler(os_kill=spy_kill)
        signaler.signal(pid=1234, sig=signal_module.SIGKILL)
        assert captured["sig"] == signal_module.SIGKILL

    def test_when_signal_called_then_correct_pid_is_forwarded_to_os_kill(self):
        captured = {}

        def spy_kill(pid, sig):
            captured["pid"] = pid

        signaler = PosixProcessSignaler(os_kill=spy_kill)
        signaler.signal(pid=5678, sig=signal_module.SIGTERM)
        assert captured["pid"] == 5678


class TestPosixProcessSignalerNeverRaises:
    def test_when_os_kill_raises_then_exception_never_propagates_to_caller(self):
        def exploding_kill(pid, sig):
            raise Exception("unexpected failure")

        signaler = PosixProcessSignaler(os_kill=exploding_kill)
        try:
            signaler.signal(pid=1234, sig=signal_module.SIGTERM)
        except Exception:
            pytest.fail("PosixProcessSignaler.signal raised into its caller")


# ============================================================================
# PosixAliveProbe
# Criterion: is_alive(pid) -> bool
#   uses os.kill(pid, 0) → True
#   ProcessLookupError → False
#   PermissionError → True (exists, not ours)
# ============================================================================


class TestPosixAliveProbeReturnValue:
    def test_when_os_kill_sig0_succeeds_then_is_alive_returns_true(self):
        # No exception from kill(pid, 0) means the process exists and we own it
        probe = PosixAliveProbe(os_kill=lambda pid, sig: None)
        assert probe.is_alive(pid=1234) is True

    def test_when_os_kill_sig0_raises_process_lookup_error_then_is_alive_returns_false(
        self,
    ):
        def stub_kill(pid, sig):
            raise ProcessLookupError("no such process")

        probe = PosixAliveProbe(os_kill=stub_kill)
        assert probe.is_alive(pid=9999) is False

    def test_when_os_kill_sig0_raises_permission_error_then_is_alive_returns_true(self):
        # PermissionError: process exists but belongs to another user — still alive
        def stub_kill(pid, sig):
            raise PermissionError("operation not permitted")

        probe = PosixAliveProbe(os_kill=stub_kill)
        assert probe.is_alive(pid=1234) is True


class TestPosixAliveProbeArgSpying:
    def test_when_is_alive_called_then_os_kill_is_invoked_with_signal_zero(self):
        captured = {}

        def spy_kill(pid, sig):
            captured["sig"] = sig

        probe = PosixAliveProbe(os_kill=spy_kill)
        probe.is_alive(pid=1234)
        assert captured["sig"] == 0

    def test_when_is_alive_called_then_correct_pid_is_forwarded_to_os_kill(self):
        captured = {}

        def spy_kill(pid, sig):
            captured["pid"] = pid

        probe = PosixAliveProbe(os_kill=spy_kill)
        probe.is_alive(pid=4321)
        assert captured["pid"] == 4321


class TestPosixAliveProbeNeverRaises:
    def test_when_os_kill_raises_unexpected_exception_then_is_alive_never_raises(self):
        def exploding_kill(pid, sig):
            raise OSError("unexpected OS error")

        probe = PosixAliveProbe(os_kill=exploding_kill)
        try:
            probe.is_alive(pid=1234)
        except Exception:
            pytest.fail("PosixAliveProbe.is_alive raised into its caller")


# ============================================================================
# Protocol duck-type conformance
# Criterion: OsascriptAppQuitter implements AppQuitter (quit method),
#            PosixProcessSignaler implements ProcessSignaler (signal method),
#            PosixAliveProbe implements AliveProbe (is_alive method)
# ============================================================================


class TestProtocolConformance:
    def test_when_osascript_app_quitter_created_then_has_callable_quit(self):
        quitter = OsascriptAppQuitter(os_runner=lambda cmd: 0)
        assert callable(getattr(quitter, "quit", None))

    def test_when_posix_process_signaler_created_then_has_callable_signal(self):
        signaler = PosixProcessSignaler(os_kill=lambda pid, sig: None)
        assert callable(getattr(signaler, "signal", None))

    def test_when_posix_alive_probe_created_then_has_callable_is_alive(self):
        probe = PosixAliveProbe(os_kill=lambda pid, sig: None)
        assert callable(getattr(probe, "is_alive", None))


# ============================================================================
# Property-based tests
# Invariants derived directly from the acceptance criteria:
#   "no shim ever raises into its caller" — total functions over stated domains
#   "ProcessLookupError → False" — universal across all valid PIDs
#   "PermissionError → True" — universal across all valid PIDs
#   "app name passed to osascript" — universal across all non-empty names
# ============================================================================


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_signaler_os_kill_raises_process_lookup_error_for_any_pid_then_returns_false(
    pid,
):
    """ProcessLookupError always maps to False, for every valid PID."""

    def stub_kill(p, s):
        raise ProcessLookupError("gone")

    signaler = PosixProcessSignaler(os_kill=stub_kill)
    assert signaler.signal(pid=pid, sig=signal_module.SIGTERM) is False


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_signaler_os_kill_succeeds_for_any_pid_then_returns_true(pid):
    """Successful os.kill always maps to True, for every valid PID."""
    signaler = PosixProcessSignaler(os_kill=lambda p, s: None)
    assert signaler.signal(pid=pid, sig=signal_module.SIGTERM) is True


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_signaler_os_kill_raises_any_exception_for_any_pid_then_never_raises(pid):
    """No shim ever raises — invariant holds for every valid PID."""

    def stub_kill(p, s):
        raise RuntimeError("some error")

    signaler = PosixProcessSignaler(os_kill=stub_kill)
    signaler.signal(pid=pid, sig=signal_module.SIGTERM)  # must not raise


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_alive_probe_os_kill_raises_process_lookup_error_for_any_pid_then_returns_false(
    pid,
):
    """ProcessLookupError always maps to False (gone), for every valid PID."""

    def stub_kill(p, s):
        raise ProcessLookupError("gone")

    probe = PosixAliveProbe(os_kill=stub_kill)
    assert probe.is_alive(pid=pid) is False


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_alive_probe_os_kill_raises_permission_error_for_any_pid_then_returns_true(
    pid,
):
    """PermissionError always maps to True (exists, not ours), for every valid PID."""

    def stub_kill(p, s):
        raise PermissionError("not ours")

    probe = PosixAliveProbe(os_kill=stub_kill)
    assert probe.is_alive(pid=pid) is True


@given(pid=st.integers(min_value=1, max_value=99999))
def test_when_alive_probe_os_kill_succeeds_for_any_pid_then_returns_true(pid):
    """Successful os.kill(pid, 0) always maps to True, for every valid PID."""
    probe = PosixAliveProbe(os_kill=lambda p, s: None)
    assert probe.is_alive(pid=pid) is True


@given(name=st.text(min_size=1, max_size=100))
def test_when_quitter_os_runner_raises_for_any_app_name_then_returns_false(name):
    """Any failure of os_runner always maps to False, for every non-empty name."""

    def stub_runner(cmd):
        raise RuntimeError("failed")

    quitter = OsascriptAppQuitter(os_runner=stub_runner)
    assert quitter.quit(pid=1234, name=name) is False


@given(name=st.text(min_size=1, max_size=100))
def test_when_quitter_succeeds_for_any_app_name_then_name_is_embedded_in_command(name):
    """The app name is always passed into the osascript command, for every non-empty name."""
    captured = {}

    def spy_runner(cmd):
        captured["cmd"] = cmd
        return 0

    quitter = OsascriptAppQuitter(os_runner=spy_runner)
    quitter.quit(pid=1234, name=name)
    assert name in captured.get("cmd", "")


@given(name=st.text(min_size=1, max_size=100))
def test_when_quitter_os_runner_raises_for_any_app_name_then_exception_never_propagates(
    name,
):
    """No shim ever raises — invariant holds for every non-empty app name."""

    def stub_runner(cmd):
        raise Exception("catastrophic")

    quitter = OsascriptAppQuitter(os_runner=stub_runner)
    quitter.quit(pid=1234, name=name)  # must not raise
