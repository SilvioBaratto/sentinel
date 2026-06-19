"""Example tests for issue #12.

feat: frontmost app detection via lsappinfo (least-privilege, no TCC) + opt-in NSWorkspace

All tests use canned lsappinfo text — no real subprocess or pyobjc call is made in pytest.
OS access is injected via a callable argument; only the observable FrontmostApp fields are
asserted, never internal parsing logic.

Assumption: LsappinfoFrontmostReader and NSWorkspaceFrontmostReader both live in
sentinel.process.frontmost; the selection factory is make_frontmost_reader in the same
module; FrontmostApp is in sentinel.domain.value_objects.
"""

from __future__ import annotations

from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Lazy imports — keeps the import graph source-blind and models deferred-import
# behaviour expected from the production code.
# ---------------------------------------------------------------------------


def _import_lsappinfo_reader():
    from sentinel.process.frontmost import LsappinfoFrontmostReader

    return LsappinfoFrontmostReader


def _import_nsworkspace_reader():
    from sentinel.process.frontmost import NSWorkspaceFrontmostReader

    return NSWorkspaceFrontmostReader


def _import_frontmost_app():
    from sentinel.domain.value_objects import FrontmostApp

    return FrontmostApp


def _import_frontmost_factory():
    """Assumption: factory is named make_frontmost_reader in sentinel.process.frontmost."""
    from sentinel.process.frontmost import make_frontmost_reader

    return make_frontmost_reader


# ---------------------------------------------------------------------------
# Canned lsappinfo output strings
# ---------------------------------------------------------------------------

# Full output — all three fields present.
_FULL_OUTPUT = (
    "ASN:0x0-0x6d06d:\n"
    ' bundleID="com.apple.Safari"\n'
    ' name="Safari"\n'
    " pid= 1234\n"
    ' ExecutionType="foreground-only"\n'
)

# Partial: name + pid present; no bundleID line.
_NO_BUNDLE_ID_OUTPUT = 'ASN:0x0-0x60123:\n name="TextEdit"\n pid= 5678\n'

# Partial: bundleID + name present; no pid line.
_NO_PID_OUTPUT = 'ASN:0x0-0x71111:\n bundleID="com.apple.TextEdit"\n name="TextEdit"\n'

# Partial: only pid present; no bundleID, no name.
_PID_ONLY_OUTPUT = "ASN:0x0-0x72222:\n pid= 9999\n"

# Completely empty string.
_EMPTY_OUTPUT = ""

# Unrecognised / garbage format.
_GARBAGE_OUTPUT = "not valid lsappinfo output\ngarbage!!!\n???\n"


def _make_reader(text: str):
    """Build an LsappinfoFrontmostReader backed by a canned callable."""
    LsappinfoFrontmostReader = _import_lsappinfo_reader()
    return LsappinfoFrontmostReader(os_runner=lambda *a, **kw: text)


# ---------------------------------------------------------------------------
# Criterion 1a: read() parses all three fields from complete lsappinfo output
# ---------------------------------------------------------------------------


class TestLsappinfoFullParsing:
    """read() → FrontmostApp with correct bundle_id, name, and pid when all fields present."""

    def test_when_full_output_then_frontmost_app_is_returned(self):
        FrontmostApp = _import_frontmost_app()
        result = _make_reader(_FULL_OUTPUT).read()
        assert isinstance(result, FrontmostApp)

    def test_when_full_output_then_bundle_id_is_parsed(self):
        result = _make_reader(_FULL_OUTPUT).read()
        assert result.bundle_id == "com.apple.Safari"

    def test_when_full_output_then_name_is_parsed(self):
        result = _make_reader(_FULL_OUTPUT).read()
        assert result.name == "Safari"

    def test_when_full_output_then_pid_is_parsed(self):
        result = _make_reader(_FULL_OUTPUT).read()
        assert result.pid == 1234

    def test_when_full_output_then_pid_is_integer(self):
        result = _make_reader(_FULL_OUTPUT).read()
        assert isinstance(result.pid, int)


# ---------------------------------------------------------------------------
# Criterion 1b: sparse / partial output yields nullable fields; never raises
# ---------------------------------------------------------------------------


class TestLsappinfoSparseParsing:
    """Partial lsappinfo text → missing fields are None; read() never raises."""

    def test_when_bundle_id_absent_then_bundle_id_is_none(self):
        result = _make_reader(_NO_BUNDLE_ID_OUTPUT).read()
        assert result.bundle_id is None

    def test_when_bundle_id_absent_then_name_and_pid_are_still_parsed(self):
        result = _make_reader(_NO_BUNDLE_ID_OUTPUT).read()
        assert result.name == "TextEdit"
        assert result.pid == 5678

    def test_when_pid_absent_then_pid_is_none(self):
        result = _make_reader(_NO_PID_OUTPUT).read()
        assert result.pid is None

    def test_when_pid_absent_then_bundle_id_and_name_are_still_parsed(self):
        result = _make_reader(_NO_PID_OUTPUT).read()
        assert result.bundle_id == "com.apple.TextEdit"
        assert result.name == "TextEdit"

    def test_when_only_pid_present_then_bundle_id_and_name_are_none(self):
        result = _make_reader(_PID_ONLY_OUTPUT).read()
        assert result.bundle_id is None
        assert result.name is None

    def test_when_output_is_garbage_then_read_does_not_raise(self):
        _make_reader(_GARBAGE_OUTPUT).read()  # must not propagate any exception

    def test_when_output_is_empty_then_read_does_not_raise(self):
        _make_reader(_EMPTY_OUTPUT).read()  # must not propagate any exception


# ---------------------------------------------------------------------------
# Criterion 2: OS access is a constructor-injected callable; no real subprocess
# ---------------------------------------------------------------------------


class TestOsCallableInjection:
    """The lsappinfo callable is injected; a fake callable is used — no real subprocess."""

    def test_when_fake_callable_injected_then_read_calls_it_exactly_once(self):
        calls: list[str] = []

        def _fake(*a, **kw) -> str:
            calls.append("called")
            return _FULL_OUTPUT

        LsappinfoFrontmostReader = _import_lsappinfo_reader()
        LsappinfoFrontmostReader(os_runner=_fake).read()
        assert calls == ["called"]

    def test_when_two_different_callables_injected_then_results_differ(self):
        """Injection is honoured — different callables produce distinct results."""
        reader_a = _make_reader(_FULL_OUTPUT)
        reader_b = _make_reader(_NO_BUNDLE_ID_OUTPUT)
        assert reader_a.read().bundle_id != reader_b.read().bundle_id

    def test_when_constructed_without_injection_then_no_error_at_construction_time(
        self,
    ):
        LsappinfoFrontmostReader = _import_lsappinfo_reader()
        LsappinfoFrontmostReader()  # default os_runner must not raise at __init__ time


# ---------------------------------------------------------------------------
# Criterion 3: NSWorkspaceFrontmostReader exists; pyobjc import is deferred
# ---------------------------------------------------------------------------


class TestNSWorkspaceDeferredImport:
    """sentinel.process is importable without pyobjc; NSWorkspaceFrontmostReader class exists."""

    def test_when_sentinel_process_package_is_imported_then_no_import_error(self):
        """Top-level package import must succeed even if pyobjc is absent."""
        import sentinel.process  # noqa: F401

    def test_when_nsworkspace_reader_class_is_imported_then_no_import_error(self):
        """Class-level import must not trigger pyobjc — the heavy import is deferred."""
        _import_nsworkspace_reader()  # must not raise ImportError

    def test_when_nsworkspace_reader_is_imported_then_class_is_not_none(self):
        NSWorkspaceFrontmostReader = _import_nsworkspace_reader()
        assert NSWorkspaceFrontmostReader is not None


# ---------------------------------------------------------------------------
# Criterion 4: Selection factory — lsappinfo by default; NSWorkspace when opted in
# ---------------------------------------------------------------------------


class TestFrontmostReaderFactory:
    """make_frontmost_reader() returns lsappinfo reader by default; NSWorkspace when True."""

    def test_when_use_nsworkspace_is_false_then_lsappinfo_reader_is_returned(self):
        make_frontmost_reader = _import_frontmost_factory()
        LsappinfoFrontmostReader = _import_lsappinfo_reader()
        reader = make_frontmost_reader(use_nsworkspace_frontmost=False)
        assert isinstance(reader, LsappinfoFrontmostReader)

    def test_when_use_nsworkspace_not_specified_then_lsappinfo_reader_is_returned(self):
        """Default is the no-TCC lsappinfo path."""
        make_frontmost_reader = _import_frontmost_factory()
        LsappinfoFrontmostReader = _import_lsappinfo_reader()
        reader = make_frontmost_reader()
        assert isinstance(reader, LsappinfoFrontmostReader)

    def test_when_use_nsworkspace_is_true_then_nsworkspace_reader_is_returned(self):
        make_frontmost_reader = _import_frontmost_factory()
        NSWorkspaceFrontmostReader = _import_nsworkspace_reader()
        reader = make_frontmost_reader(use_nsworkspace_frontmost=True)
        assert isinstance(reader, NSWorkspaceFrontmostReader)


# ---------------------------------------------------------------------------
# Criterion 5: Unresolvable frontmost → FrontmostApp(None, None, None) — fail-safe
# ---------------------------------------------------------------------------


class TestFailSafeFrontmostApp:
    """Unresolvable output or OS error → all three fields None; never propagates."""

    def test_when_output_is_empty_then_bundle_id_is_none(self):
        assert _make_reader(_EMPTY_OUTPUT).read().bundle_id is None

    def test_when_output_is_empty_then_name_is_none(self):
        assert _make_reader(_EMPTY_OUTPUT).read().name is None

    def test_when_output_is_empty_then_pid_is_none(self):
        assert _make_reader(_EMPTY_OUTPUT).read().pid is None

    def test_when_output_is_garbage_then_all_fields_are_none(self):
        result = _make_reader(_GARBAGE_OUTPUT).read()
        assert result.bundle_id is None
        assert result.name is None
        assert result.pid is None

    def test_when_os_callable_raises_then_all_fields_are_none(self):
        """If the OS callable itself raises, read() must return the fail-safe triple."""
        LsappinfoFrontmostReader = _import_lsappinfo_reader()

        def _raising(*a, **kw) -> str:
            raise OSError("lsappinfo not found")

        result = LsappinfoFrontmostReader(os_runner=_raising).read()
        assert result.bundle_id is None
        assert result.name is None
        assert result.pid is None

    def test_when_os_callable_raises_then_no_exception_propagates(self):
        LsappinfoFrontmostReader = _import_lsappinfo_reader()

        def _raising(*a, **kw) -> str:
            raise RuntimeError("unexpected OS error")

        LsappinfoFrontmostReader(os_runner=_raising).read()  # must not raise

    def test_when_result_is_fail_safe_then_equals_frontmost_app_none_none_none(self):
        FrontmostApp = _import_frontmost_app()
        result = _make_reader(_EMPTY_OUTPUT).read()
        assert result == FrontmostApp(None, None, None)


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------


class TestReadNeverRaisesProperty:
    """Invariant: read() is a total function over all string lsappinfo outputs."""

    @given(st.text())
    def test_when_any_string_is_used_as_lsappinfo_output_then_read_does_not_raise(
        self, lsappinfo_text: str
    ):
        """Criterion: 'never raises into the detector' — must hold for every string input."""
        _make_reader(lsappinfo_text).read()  # must not raise


class TestReadAlwaysReturnsFrontmostAppProperty:
    """Invariant: read() always returns a FrontmostApp regardless of input text."""

    @given(st.text())
    def test_when_any_string_output_is_given_then_result_is_frontmost_app(
        self, lsappinfo_text: str
    ):
        FrontmostApp = _import_frontmost_app()
        result = _make_reader(lsappinfo_text).read()
        assert isinstance(result, FrontmostApp)


class TestFactoryDeterminismProperty:
    """Invariant: factory selection is deterministic — same flag → same reader type."""

    @given(st.booleans())
    def test_when_factory_called_with_boolean_flag_then_correct_reader_type_is_returned(
        self, use_ns: bool
    ):
        make_frontmost_reader = _import_frontmost_factory()
        LsappinfoFrontmostReader = _import_lsappinfo_reader()
        NSWorkspaceFrontmostReader = _import_nsworkspace_reader()
        reader = make_frontmost_reader(use_nsworkspace_frontmost=use_ns)
        if use_ns:
            assert isinstance(reader, NSWorkspaceFrontmostReader)
        else:
            assert isinstance(reader, LsappinfoFrontmostReader)
