"""
Tests for DenyListPathGuard — issue #23: disk path deny-list + safety predicate (PathGuard).

Derived directly from the acceptance criteria. No implementation source was read.
All tests are in Red phase: they must fail until the implementation exists and is correct.
"""

import os
import tempfile
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st

from sentinel.domain.protocols import PathGuard
from sentinel.execute.deny_list_path_guard import DenyListPathGuard

_HOME = os.path.expanduser("~")
_APP_SUPPORT = os.path.join(_HOME, "Library", "Application Support")


# ---------------------------------------------------------------------------
# Criterion: DenyListPathGuard implements PathGuard: is_safe(path) -> bool
# ---------------------------------------------------------------------------


class TestDenyListPathGuardProtocolConformance:
    def test_when_guard_is_created_then_is_safe_method_exists_and_is_callable(self):
        guard = DenyListPathGuard()
        assert hasattr(guard, "is_safe") and callable(guard.is_safe)

    def test_when_is_safe_is_called_then_a_bool_is_returned(self):
        guard = DenyListPathGuard()
        result = guard.is_safe(os.path.join(_HOME, "Downloads", "test.zip"))
        assert isinstance(result, bool)

    def test_when_guard_is_used_as_path_guard_then_isinstance_check_passes(self):
        # PathGuard must be @runtime_checkable for this to work; if not, the
        # implementation is expected to satisfy the protocol structurally.
        guard = DenyListPathGuard()
        assert isinstance(guard, PathGuard)


# ---------------------------------------------------------------------------
# Criterion: Returns False for system/protected paths
# ---------------------------------------------------------------------------


class TestDenyListPathGuardDeniedPaths:
    """
    Acceptance criterion: Returns False for /System (and children), /usr, /bin,
    /sbin, ~/Library/Application Support (and children), any .app bundle path,
    root /, and empty string.
    """

    def setup_method(self):
        self.guard = DenyListPathGuard()

    # /System and children

    def test_when_path_is_system_then_false_is_returned(self):
        assert self.guard.is_safe("/System") is False

    def test_when_path_is_child_of_system_then_false_is_returned(self):
        assert self.guard.is_safe("/System/Library/CoreServices/Finder.app") is False

    def test_when_path_is_deeply_nested_under_system_then_false_is_returned(self):
        assert (
            self.guard.is_safe(
                "/System/Library/Frameworks/Python.framework/Versions/3.11"
            )
            is False
        )

    # /usr

    def test_when_path_is_usr_then_false_is_returned(self):
        assert self.guard.is_safe("/usr") is False

    def test_when_path_is_child_of_usr_then_false_is_returned(self):
        assert self.guard.is_safe("/usr/local/bin/python3") is False

    # /bin

    def test_when_path_is_bin_then_false_is_returned(self):
        assert self.guard.is_safe("/bin") is False

    def test_when_path_is_child_of_bin_then_false_is_returned(self):
        assert self.guard.is_safe("/bin/bash") is False

    # /sbin

    def test_when_path_is_sbin_then_false_is_returned(self):
        assert self.guard.is_safe("/sbin") is False

    def test_when_path_is_child_of_sbin_then_false_is_returned(self):
        assert self.guard.is_safe("/sbin/launchd") is False

    # ~/Library/Application Support and children (expanded form)

    def test_when_path_is_application_support_then_false_is_returned(self):
        assert self.guard.is_safe(_APP_SUPPORT) is False

    def test_when_path_is_child_of_application_support_then_false_is_returned(self):
        child = os.path.join(_APP_SUPPORT, "SomeApp", "database.db")
        assert self.guard.is_safe(child) is False

    def test_when_path_is_tilde_application_support_then_false_is_returned(self):
        # Guard must handle tilde form as a convenience (expanded or denied directly).
        assert self.guard.is_safe("~/Library/Application Support") is False

    def test_when_path_is_tilde_child_of_application_support_then_false_is_returned(
        self,
    ):
        assert (
            self.guard.is_safe("~/Library/Application Support/SomeApp/prefs.plist")
            is False
        )

    # .app bundle paths

    def test_when_path_is_app_bundle_root_then_false_is_returned(self):
        assert self.guard.is_safe("/Applications/Safari.app") is False

    def test_when_path_is_inside_app_bundle_then_false_is_returned(self):
        assert (
            self.guard.is_safe(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            )
            is False
        )

    def test_when_path_is_user_applications_app_bundle_then_false_is_returned(self):
        app = os.path.join(_HOME, "Applications", "MyApp.app")
        assert self.guard.is_safe(app) is False

    # Root and empty string

    def test_when_path_is_filesystem_root_then_false_is_returned(self):
        assert self.guard.is_safe("/") is False

    def test_when_path_is_empty_string_then_false_is_returned(self):
        assert self.guard.is_safe("") is False


# ---------------------------------------------------------------------------
# Criterion: Normalizes with os.path.realpath/abspath so .. traversal → False
# ---------------------------------------------------------------------------


class TestDenyListPathGuardNormalization:
    """
    Acceptance criterion: Normalizes with os.path.realpath/abspath so symlink or
    .. traversal that resolves into a deny dir → False.
    """

    def setup_method(self):
        self.guard = DenyListPathGuard()

    def test_when_path_uses_dotdot_traversal_into_system_then_false_is_returned(self):
        # /tmp/../System resolves to /System via abspath — must be denied.
        assert self.guard.is_safe("/tmp/../System") is False

    def test_when_path_uses_dotdot_traversal_into_bin_then_false_is_returned(self):
        assert self.guard.is_safe("/usr/../bin") is False

    def test_when_path_uses_dotdot_traversal_into_usr_then_false_is_returned(self):
        assert self.guard.is_safe("/usr/local/../../usr") is False

    def test_when_path_uses_dotdot_traversal_into_application_support_then_false_is_returned(
        self,
    ):
        traversal = os.path.join(
            _HOME, "Downloads", "..", "Library", "Application Support"
        )
        assert self.guard.is_safe(traversal) is False

    def test_when_path_is_symlink_into_system_then_false_is_returned(self):
        # Create a real symlink that points into /System; the guard must resolve it.
        with tempfile.TemporaryDirectory() as tmpdir:
            link_path = os.path.join(tmpdir, "system_link")
            os.symlink("/System", link_path)
            assert self.guard.is_safe(link_path) is False

    def test_when_path_is_symlink_child_into_bin_then_false_is_returned(self):
        # Symlink resolves to /bin/bash — must be denied.
        with tempfile.TemporaryDirectory() as tmpdir:
            link_path = os.path.join(tmpdir, "bash_link")
            os.symlink("/bin/bash", link_path)
            assert self.guard.is_safe(link_path) is False


# ---------------------------------------------------------------------------
# Criterion: Returns True for legitimate targets
# ---------------------------------------------------------------------------


class TestDenyListPathGuardSafePaths:
    """
    Acceptance criterion: Returns True for ~/Library/Caches/<App>,
    ~/Downloads/old.zip, ~/dev/<project>/node_modules.
    """

    def setup_method(self):
        self.guard = DenyListPathGuard()

    def test_when_path_is_user_library_caches_app_then_true_is_returned(self):
        safe = os.path.join(_HOME, "Library", "Caches", "com.example.MyApp")
        assert self.guard.is_safe(safe) is True

    def test_when_path_is_nested_inside_user_library_caches_then_true_is_returned(self):
        safe = os.path.join(_HOME, "Library", "Caches", "com.example.MyApp", "Cache.db")
        assert self.guard.is_safe(safe) is True

    def test_when_path_is_downloads_file_then_true_is_returned(self):
        safe = os.path.join(_HOME, "Downloads", "old.zip")
        assert self.guard.is_safe(safe) is True

    def test_when_path_is_dev_project_node_modules_then_true_is_returned(self):
        safe = os.path.join(_HOME, "dev", "myproject", "node_modules")
        assert self.guard.is_safe(safe) is True

    def test_when_path_is_dev_project_pycache_then_true_is_returned(self):
        safe = os.path.join(_HOME, "dev", "myproject", "src", "__pycache__")
        assert self.guard.is_safe(safe) is True


# ---------------------------------------------------------------------------
# Criterion: Any exception during normalization → False (fail-safe = deny)
# ---------------------------------------------------------------------------


class TestDenyListPathGuardFailSafe:
    """
    Acceptance criterion: Any exception during normalization → False.
    Fail-safe: when the guard cannot determine whether a path is safe, it
    must deny access (return False), never raise.
    """

    def test_when_realpath_raises_os_error_then_false_is_returned(self):
        guard = DenyListPathGuard()
        # Patch os.path.realpath globally; works for any code using os.path.realpath(...).
        with patch("os.path.realpath", side_effect=OSError("disk I/O error")):
            result = guard.is_safe("/tmp/some_path")
        assert result is False

    def test_when_realpath_raises_value_error_then_false_is_returned(self):
        guard = DenyListPathGuard()
        with patch("os.path.realpath", side_effect=ValueError("bad path")):
            result = guard.is_safe("/tmp/some_path")
        assert result is False

    def test_when_abspath_raises_os_error_then_false_is_returned(self):
        guard = DenyListPathGuard()
        with patch("os.path.abspath", side_effect=OSError("disk I/O error")):
            result = guard.is_safe("/tmp/some_path")
        assert result is False

    def test_when_normalization_raises_any_exception_then_guard_does_not_raise(self):
        guard = DenyListPathGuard()
        with patch("os.path.realpath", side_effect=Exception("unexpected")):
            try:
                result = guard.is_safe("/tmp/whatever")
            except Exception as exc:
                pytest.fail(f"guard raised instead of returning False: {exc}")
            else:
                assert result is False


# ---------------------------------------------------------------------------
# Property-based test (derived from criterion 2: "Returns False for ... and children")
#
# Invariant — monotonicity of denial: for ANY valid path suffix appended to a
# deny-listed prefix, the guard must return False.  This is an ordering /
# monotonicity property: once a prefix is denied, all children are denied.
# ---------------------------------------------------------------------------

_DENY_PREFIXES = [
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    _APP_SUPPORT,
]

_PATH_SEGMENT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=1,
    max_size=30,
).filter(lambda s: s not in (".", ".."))


@given(
    prefix=st.sampled_from(_DENY_PREFIXES),
    segments=st.lists(_PATH_SEGMENT, min_size=0, max_size=4),
)
@settings(max_examples=200)
def test_when_path_starts_with_deny_prefix_then_false_is_returned(prefix, segments):
    """
    Invariant (criterion 2 — 'and children'): prepending any deny-listed prefix
    to an arbitrary path suffix must always produce a denied (False) result.
    This property exercises children at arbitrary depth.
    """
    guard = DenyListPathGuard()
    path = os.path.join(prefix, *segments) if segments else prefix
    assert guard.is_safe(path) is False
