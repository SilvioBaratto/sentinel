"""Tests for MacNotifier and NullNotifier — authored from Issue #27 acceptance criteria.

Red-phase TDD: written before any implementation exists, source-blind.

Assumptions (documented where criteria text is ambiguous):
- MacNotifier and NullNotifier live in sentinel.notify.notifier.
- Notifier is a Protocol in sentinel.domain.protocols with
    notify(result: ActionResult) -> None
- ActionResult is a value object in sentinel.domain.value_objects with fields:
    target: str, bytes_freed: int, success: bool
  (The notifier criterion only names target and bytes_freed; success is inferred
  from the parallel AuditRecord shape and requirements.md section 8.)
- MacNotifier(os_runner=<callable>) — injects the OS invocation callable so unit
  tests never touch the real osascript binary.  The criterion text says
  "runs an injected os_runner (osascript display notification)".
- os_runner is called with a single string argument containing the osascript command;
  target and formatted bytes_freed must both appear in that string.
- Human-readable size uses SI decimal units (same as AuditLogger): 1_200_000_000
  bytes -> "1.2 GB".
- NullNotifier() accepts no os_runner and never invokes subprocess or os.system —
  verified by patching both in the test.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hypothesis import given, strategies as st

import sentinel.notify.notifier as notifier_mod
from sentinel.domain.value_objects import ActionKind, ActionResult, Reversibility
from sentinel.notify.notifier import MacNotifier, NullNotifier


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _make_result(
    target: str = "Chrome",
    bytes_freed: int = 1_200_000_000,
    success: bool = True,
) -> ActionResult:
    return ActionResult(
        kind=ActionKind.KILL_PROCESS,
        target=target,
        success=success,
        reversibility=Reversibility.REVERSIBLE,
        bytes_freed=bytes_freed,
    )


# ===========================================================================
# Criterion 3 — MacNotifier implements Notifier
# "notify(result: ActionResult) -> None; builds a message from the result
#  (target + formatted bytes_freed) and runs an injected os_runner; swallows failures"
# ===========================================================================


class TestMacNotifierProtocol:
    def test_when_notify_called_then_return_value_is_none(self) -> None:
        """notify() must return None — the Notifier protocol signature."""
        notifier = MacNotifier(os_runner=MagicMock())
        result = notifier.notify(_make_result())
        assert result is None

    def test_when_mac_notifier_created_then_it_satisfies_notifier_protocol(
        self,
    ) -> None:
        """MacNotifier must expose a callable 'notify' attribute."""
        notifier = MacNotifier(os_runner=MagicMock())
        assert hasattr(notifier, "notify")
        assert callable(notifier.notify)


class TestMacNotifierOsRunner:
    def test_when_result_notified_then_os_runner_is_called_exactly_once(
        self,
    ) -> None:
        """os_runner must be invoked once per notify() call."""
        os_runner = MagicMock()
        MacNotifier(os_runner=os_runner).notify(_make_result())
        os_runner.assert_called_once()

    def test_when_result_notified_then_os_runner_call_contains_target(self) -> None:
        """The os_runner argument must include the action target."""
        received: list[object] = []
        MacNotifier(os_runner=lambda *a, **kw: received.append((a, kw))).notify(
            _make_result(target="Slack")
        )
        assert received, "os_runner was never called"
        call_str = str(received[0])
        assert "Slack" in call_str

    def test_when_result_notified_then_os_runner_call_contains_formatted_bytes(
        self,
    ) -> None:
        """The os_runner argument must include a human-readable bytes_freed string.

        Assumption: 1_200_000_000 bytes is formatted as '1.2 GB' (SI decimal),
        matching the AuditLogger criterion example.
        """
        received: list[object] = []
        MacNotifier(os_runner=lambda *a, **kw: received.append((a, kw))).notify(
            _make_result(bytes_freed=1_200_000_000)
        )
        assert received, "os_runner was never called"
        call_str = str(received[0])
        assert "1.2 GB" in call_str

    def test_when_result_notified_then_os_runner_call_contains_osascript_invocation(
        self,
    ) -> None:
        """The command passed to os_runner must reference osascript or display notification."""
        received: list[object] = []
        MacNotifier(os_runner=lambda *a, **kw: received.append((a, kw))).notify(
            _make_result()
        )
        call_str = str(received[0])
        assert "osascript" in call_str or "display notification" in call_str


class TestMacNotifierSwallowsFailures:
    def test_when_os_runner_raises_then_notify_does_not_propagate_exception(
        self,
    ) -> None:
        """A failing os_runner must not cause notify() to raise."""

        def _failing(*args: object, **kwargs: object) -> None:
            raise OSError("osascript binary not found")

        MacNotifier(os_runner=_failing).notify(_make_result())  # must not raise

    # ---- Property: notify() never raises for any target/bytes_freed when runner fails ----

    @given(
        target=st.text(min_size=1, max_size=100).filter(
            lambda s: "\n" not in s and "\r" not in s
        ),
        bytes_freed=st.integers(min_value=0, max_value=10 * 1024**3),
    )
    def test_when_os_runner_raises_and_inputs_vary_then_notify_never_raises(
        self, target: str, bytes_freed: int
    ) -> None:
        """Invariant: notify() swallows the runner exception for all valid ActionResult inputs.

        Derived from criterion: 'swallows failures'.
        """

        def _always_raises(*args: object, **kwargs: object) -> None:
            raise RuntimeError("system failure")

        notifier = MacNotifier(os_runner=_always_raises)
        notifier.notify(
            ActionResult(
                kind=ActionKind.KILL_PROCESS,
                target=target,
                success=True,
                reversibility=Reversibility.REVERSIBLE,
                bytes_freed=bytes_freed,
            )
        )  # must not raise


# ===========================================================================
# Fix (fix/exec-safety) — MacNotifier must NOT be shell-injectable
# result.target can be an attacker-influenced file path (e.g. a stale Download
# trashed during DISK_LOW cleanup).  The notifier must never run it through a
# shell, and must escape it inside the AppleScript string literal.
# ===========================================================================


class TestMacNotifierShellSafety:
    def test_default_os_runner_does_not_use_shell(self) -> None:
        """The real os_runner must pass an argv LIST and never shell=True."""
        with patch.object(notifier_mod.subprocess, "run") as mock_run:
            notifier_mod._default_os_runner(["osascript", "-e", "noop"])
        mock_run.assert_called_once()
        call_args, call_kwargs = mock_run.call_args
        assert isinstance(call_args[0], list), "os_runner must pass an argv list"
        assert call_kwargs.get("shell", False) is False, "shell=True is forbidden"

    def test_when_target_has_metacharacters_then_runner_gets_a_list_not_a_string(
        self,
    ) -> None:
        captured: list[object] = []
        evil = 'X"; rm -rf ~ #'
        MacNotifier(os_runner=lambda arg: captured.append(arg)).notify(
            _make_result(target=evil)
        )
        assert captured, "os_runner was never called"
        assert isinstance(captured[0], list), "runner must receive argv list, not a shell string"

    def test_when_target_has_double_quotes_then_they_are_escaped_in_the_script(
        self,
    ) -> None:
        """All quotes from target/title must be backslash-escaped; only the 4
        structural AppleScript quotes stay unescaped."""
        captured: list[list[str]] = []
        evil = 'A" with title "B'  # 2 embedded quotes that try to break the literal
        MacNotifier(os_runner=lambda arg: captured.append(arg)).notify(
            _make_result(target=evil)
        )
        script = captured[0][-1]
        assert '\\"' in script, "embedded quotes must be escaped"
        structural = script.count('"') - script.count('\\"')
        assert structural == 4, (
            f"expected exactly 4 structural quotes, found {structural}: {script!r}"
        )


# ===========================================================================
# Criterion 4 — NullNotifier implements Notifier, issues no OS call
# "NullNotifier implements Notifier and issues no OS call (spy call_count 0)
#  — for dry-run / NotifyConfig.enabled=False"
# ===========================================================================


class TestNullNotifierProtocol:
    def test_when_notify_called_then_return_value_is_none(self) -> None:
        """NullNotifier.notify() must return None."""
        result = NullNotifier().notify(_make_result())
        assert result is None

    def test_when_null_notifier_created_then_it_satisfies_notifier_protocol(
        self,
    ) -> None:
        """NullNotifier must expose a callable 'notify' attribute."""
        notifier = NullNotifier()
        assert hasattr(notifier, "notify")
        assert callable(notifier.notify)


class TestNullNotifierIssuesNoOsCall:
    def test_when_null_notifier_notified_then_subprocess_run_is_not_called(
        self,
    ) -> None:
        """NullNotifier must not invoke subprocess.run (spy call_count 0)."""
        with patch("subprocess.run") as mock_run:
            NullNotifier().notify(_make_result())
            assert mock_run.call_count == 0

    def test_when_null_notifier_notified_then_subprocess_popen_is_not_called(
        self,
    ) -> None:
        """NullNotifier must not invoke subprocess.Popen (spy call_count 0)."""
        with patch("subprocess.Popen") as mock_popen:
            NullNotifier().notify(_make_result())
            assert mock_popen.call_count == 0

    def test_when_null_notifier_notified_then_os_system_is_not_called(self) -> None:
        """NullNotifier must not invoke os.system (spy call_count 0)."""
        with patch("os.system") as mock_system:
            NullNotifier().notify(_make_result())
            assert mock_system.call_count == 0

    def test_when_null_notifier_notified_multiple_times_then_no_os_call_ever_made(
        self,
    ) -> None:
        """Multiple notify() calls must still produce zero OS invocations."""
        with patch("subprocess.run") as mock_run, patch("os.system") as mock_system:
            notifier = NullNotifier()
            for i in range(5):
                notifier.notify(_make_result(target=f"app_{i}"))
            assert mock_run.call_count == 0
            assert mock_system.call_count == 0
