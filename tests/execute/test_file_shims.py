"""Tests for Issue #24 — Trash-mover + permanent-deleter OS shims.

Design note: FileManagerTrasher does NOT fall back to permanent delete on trash
failure.  On failure it returns success=False with reversibility=REVERSIBLE so
the audit log preserves the caller's reversible intent (per the reversibility
regression fix documented in the issue comment).  OsRemoveDeleter is the
explicit permanent-delete path that callers opt into for regenerable artifacts.

Module: sentinel.execute.file_shims
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

import sentinel.execute.file_shims as _module
from sentinel.domain.protocols import Deleter, Trasher
from sentinel.domain.value_objects import ActionKind, ActionResult, Reversibility
from sentinel.execute.file_shims import (
    FileManagerTrasher,
    OsRemoveDeleter,
    _dir_size,
    _pyobjc_trash,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _noop_trash(path: str) -> None:
    """Stub trash_fn that succeeds silently."""


def _failing_trash(path: str) -> None:
    """Stub trash_fn that always raises."""
    raise OSError("simulated trash failure")


def _make_trasher(trash_fn=None) -> FileManagerTrasher:
    return FileManagerTrasher(
        trash_fn=trash_fn if trash_fn is not None else _noop_trash
    )


def _mock_foundation():
    """Return (mock_foundation, mock_fm_instance, mock_nsfilemanager) for sys.modules injection."""
    mock_fm_instance = MagicMock()
    mock_fm_instance.trashItemAtURL_resultingItemURL_error_.return_value = (
        True,
        MagicMock(),
        None,
    )
    mock_nsfilemanager = MagicMock()
    mock_nsfilemanager.defaultManager.return_value = mock_fm_instance
    mock_nsurl_class = MagicMock()
    mock_nsurl_class.fileURLWithPath_.return_value = MagicMock()
    mock_foundation = MagicMock()
    mock_foundation.NSFileManager = mock_nsfilemanager
    mock_foundation.NSURL = mock_nsurl_class
    return mock_foundation, mock_fm_instance, mock_nsfilemanager


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 1 — FileManagerTrasher implements Trasher
# trash(path) -> ActionResult; sizes path; attempts trash_fn;
# reversibility=REVERSIBLE on success; on failure success=False REVERSIBLE;
# kind=TRASH always; never raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestFileManagerTrasherProtocol:
    def test_when_file_manager_trasher_is_created_then_it_has_callable_trash(self):
        assert callable(getattr(_make_trasher(), "trash", None))

    def test_when_file_manager_trasher_is_created_then_isinstance_trasher_returns_true(
        self,
    ):
        assert isinstance(_make_trasher(), Trasher)

    def test_when_trash_is_called_then_an_action_result_is_returned(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        result = _make_trasher().trash(str(f))
        assert isinstance(result, ActionResult)


class TestFileManagerTrasherSuccessPath:
    def test_when_trash_fn_succeeds_then_result_success_is_true(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert _make_trasher(trash_fn=_noop_trash).trash(str(f)).success is True

    def test_when_trash_fn_succeeds_then_result_kind_is_trash(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert (
            _make_trasher(trash_fn=_noop_trash).trash(str(f)).kind is ActionKind.TRASH
        )

    def test_when_trash_fn_succeeds_then_result_reversibility_is_reversible(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert (
            _make_trasher(trash_fn=_noop_trash).trash(str(f)).reversibility
            is Reversibility.REVERSIBLE
        )

    def test_when_trash_fn_succeeds_then_bytes_freed_is_nonnegative(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"x" * 200)
        assert _make_trasher(trash_fn=_noop_trash).trash(str(f)).bytes_freed >= 0

    def test_when_trash_fn_succeeds_with_file_of_known_size_then_bytes_freed_matches(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_bytes(b"z" * 512)
        assert _make_trasher(trash_fn=_noop_trash).trash(str(f)).bytes_freed == 512


class TestFileManagerTrasherFailurePath:
    """On trash_fn failure: success=False, reversibility=REVERSIBLE, detail non-empty.

    No silent escalation to permanent delete (reversibility-invariant fix from issue comment).
    """

    def test_when_trash_fn_raises_then_result_success_is_false(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert _make_trasher(trash_fn=_failing_trash).trash(str(f)).success is False

    def test_when_trash_fn_raises_then_result_kind_is_trash(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert (
            _make_trasher(trash_fn=_failing_trash).trash(str(f)).kind
            is ActionKind.TRASH
        )

    def test_when_trash_fn_raises_then_result_reversibility_is_reversible(
        self, tmp_path
    ):
        """Failure preserves REVERSIBLE — no silent escalation to PERMANENT delete."""
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert (
            _make_trasher(trash_fn=_failing_trash).trash(str(f)).reversibility
            is Reversibility.REVERSIBLE
        )

    def test_when_trash_fn_raises_then_result_detail_is_nonempty(self, tmp_path):
        """detail explains the trash failure for the audit log."""
        f = tmp_path / "a.txt"
        f.write_text("content")
        assert _make_trasher(trash_fn=_failing_trash).trash(str(f)).detail

    def test_when_trash_fn_raises_then_bytes_freed_is_zero(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"x" * 100)
        assert _make_trasher(trash_fn=_failing_trash).trash(str(f)).bytes_freed == 0


class TestFileManagerTrasherNeverRaises:
    def test_when_trash_fn_raises_os_error_then_trash_does_not_propagate(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_text("content")

        def explode(p):
            raise OSError("permission denied")

        try:
            _make_trasher(trash_fn=explode).trash(str(f))
        except Exception:
            pytest.fail("FileManagerTrasher.trash propagated OSError into caller")

    def test_when_path_does_not_exist_then_trash_does_not_raise(self):
        try:
            _make_trasher().trash("/nonexistent/path/xyz123sentinel")
        except Exception:
            pytest.fail("FileManagerTrasher.trash raised on nonexistent path")

    def test_when_trash_fn_raises_runtime_error_then_trash_does_not_propagate(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_text("content")

        def explode(p):
            raise RuntimeError("unexpected failure")

        try:
            _make_trasher(trash_fn=explode).trash(str(f))
        except Exception:
            pytest.fail("FileManagerTrasher.trash propagated RuntimeError into caller")


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 2 — Default trash_fn (_pyobjc_trash)
# calls NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(...);
# NO NSFileCoordinator anywhere; PyObjC imported deferred inside the function
# ═══════════════════════════════════════════════════════════════════════════════


class TestPyobjcTrashDeferredImport:
    def test_when_file_shims_module_is_imported_then_foundation_is_not_in_module_namespace(
        self,
    ):
        """Foundation must be imported lazily inside _pyobjc_trash, not at module level."""
        assert "Foundation" not in vars(_module)

    def test_when_file_shims_module_is_imported_then_nsfilemanager_is_not_in_module_namespace(
        self,
    ):
        assert "NSFileManager" not in vars(_module)

    def test_when_file_shims_module_is_imported_then_pyobjc_trash_is_callable(self):
        assert callable(_pyobjc_trash)


class TestPyobjcTrashCallBehavior:
    def test_when_pyobjc_trash_is_called_then_nsfilemanager_default_manager_is_invoked(
        self,
    ):
        foundation, _, mock_nsfilemanager = _mock_foundation()
        with patch.dict(sys.modules, {"Foundation": foundation}):
            _pyobjc_trash("/some/path")
        mock_nsfilemanager.defaultManager.assert_called()

    def test_when_pyobjc_trash_is_called_then_trash_item_at_url_method_is_invoked(self):
        foundation, mock_fm_instance, _ = _mock_foundation()
        with patch.dict(sys.modules, {"Foundation": foundation}):
            _pyobjc_trash("/some/path")
        mock_fm_instance.trashItemAtURL_resultingItemURL_error_.assert_called_once()

    def test_when_pyobjc_trash_is_called_then_nsfilecoordinator_is_not_instantiated(
        self,
    ):
        """Criterion: NO NSFileCoordinator anywhere — it must not be called/constructed."""
        foundation, _, _ = _mock_foundation()
        with patch.dict(sys.modules, {"Foundation": foundation}):
            _pyobjc_trash("/some/path")
        assert not foundation.NSFileCoordinator.called


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 3 — OsRemoveDeleter implements Deleter
# delete(path) -> ActionResult; sizes then removes (dir→rmtree / file→remove);
# reversibility=PERMANENT; kind=DELETE; nonexistent path → success=False, no raise
# ═══════════════════════════════════════════════════════════════════════════════


class TestOsRemoveDeleterProtocol:
    def test_when_os_remove_deleter_is_created_then_it_has_callable_delete(self):
        deleter = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None)
        assert callable(getattr(deleter, "delete", None))

    def test_when_os_remove_deleter_is_created_then_isinstance_deleter_returns_true(
        self,
    ):
        deleter = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None)
        assert isinstance(deleter, Deleter)

    def test_when_delete_is_called_then_an_action_result_is_returned(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("data")
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert isinstance(result, ActionResult)


class TestOsRemoveDeleterDispatch:
    def test_when_path_is_a_file_then_remove_fn_is_called(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("data")
        called = {}

        def spy_remove(p):
            called["path"] = p

        OsRemoveDeleter(rmtree=lambda p: None, remove=spy_remove).delete(str(f))
        assert called.get("path") == str(f)

    def test_when_path_is_a_file_then_rmtree_fn_is_not_called(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("data")
        rmtree_called = {"flag": False}

        def spy_rmtree(p):
            rmtree_called["flag"] = True

        OsRemoveDeleter(rmtree=spy_rmtree, remove=lambda p: None).delete(str(f))
        assert not rmtree_called["flag"]

    def test_when_path_is_a_directory_then_rmtree_fn_is_called(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        called = {}

        def spy_rmtree(p):
            called["path"] = p

        OsRemoveDeleter(rmtree=spy_rmtree, remove=lambda p: None).delete(str(d))
        assert called.get("path") == str(d)

    def test_when_path_is_a_directory_then_remove_fn_is_not_called(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        remove_called = {"flag": False}

        def spy_remove(p):
            remove_called["flag"] = True

        OsRemoveDeleter(rmtree=lambda p: None, remove=spy_remove).delete(str(d))
        assert not remove_called["flag"]


class TestOsRemoveDeleterResultConstants:
    def test_when_delete_succeeds_then_result_kind_is_delete(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("data")
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert result.kind is ActionKind.DELETE

    def test_when_delete_succeeds_then_result_reversibility_is_permanent(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_text("data")
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert result.reversibility is Reversibility.PERMANENT

    def test_when_delete_succeeds_then_result_success_is_true(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("data")
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert result.success is True

    def test_when_delete_succeeds_then_bytes_freed_is_nonnegative(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"y" * 300)
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert result.bytes_freed >= 0

    def test_when_delete_is_called_on_file_of_known_size_then_bytes_freed_matches(
        self, tmp_path
    ):
        f = tmp_path / "a.txt"
        f.write_bytes(b"a" * 256)
        result = OsRemoveDeleter(rmtree=lambda p: None, remove=lambda p: None).delete(
            str(f)
        )
        assert result.bytes_freed == 256


class TestOsRemoveDeleterNonexistentPath:
    def test_when_path_does_not_exist_then_delete_returns_success_false(self, tmp_path):
        nonexistent = str(tmp_path / "ghost_file_xyz")
        result = OsRemoveDeleter(rmtree=shutil.rmtree, remove=os.remove).delete(
            nonexistent
        )
        assert result.success is False

    def test_when_path_does_not_exist_then_delete_does_not_raise(self, tmp_path):
        nonexistent = str(tmp_path / "ghost_file_xyz")
        try:
            OsRemoveDeleter(rmtree=shutil.rmtree, remove=os.remove).delete(nonexistent)
        except Exception:
            pytest.fail("OsRemoveDeleter.delete raised on nonexistent path")

    def test_when_path_does_not_exist_then_delete_result_kind_is_delete(self, tmp_path):
        nonexistent = str(tmp_path / "ghost_file_xyz")
        result = OsRemoveDeleter(rmtree=shutil.rmtree, remove=os.remove).delete(
            nonexistent
        )
        assert result.kind is ActionKind.DELETE

    def test_when_path_does_not_exist_then_delete_result_reversibility_is_permanent(
        self, tmp_path
    ):
        nonexistent = str(tmp_path / "ghost_file_xyz")
        result = OsRemoveDeleter(rmtree=shutil.rmtree, remove=os.remove).delete(
            nonexistent
        )
        assert result.reversibility is Reversibility.PERMANENT


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 4 — _dir_size helper
# best-effort; per-entry try/except → skip; missing path → 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestDirSizeHelper:
    def test_when_path_does_not_exist_then_dir_size_returns_zero(self):
        assert _dir_size("/sentinel/definitely/nonexistent/xyz123abc") == 0

    def test_when_path_is_a_file_then_dir_size_returns_file_size(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"x" * 100)
        assert _dir_size(str(f)) == 100

    def test_when_path_is_an_empty_directory_then_dir_size_returns_zero(self, tmp_path):
        d = tmp_path / "empty_dir"
        d.mkdir()
        assert _dir_size(str(d)) == 0

    def test_when_directory_has_one_file_then_dir_size_returns_that_files_size(
        self, tmp_path
    ):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "only.bin").write_bytes(b"z" * 150)
        assert _dir_size(str(d)) == 150

    def test_when_directory_has_multiple_files_then_dir_size_returns_sum_of_sizes(
        self, tmp_path
    ):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "a.bin").write_bytes(b"a" * 100)
        (d / "b.bin").write_bytes(b"b" * 200)
        assert _dir_size(str(d)) == 300

    def test_when_one_entry_raises_on_stat_then_remaining_entries_are_still_summed(
        self, tmp_path, monkeypatch
    ):
        """per-entry try/except → skip: a stat error on one entry must not abort the total."""
        d = tmp_path / "dir"
        d.mkdir()
        (d / "good.bin").write_bytes(b"g" * 100)

        good_stat = MagicMock()
        good_stat.st_size = 100
        good_entry = MagicMock()
        good_entry.path = str(d / "good.bin")
        good_entry.name = "good.bin"
        good_entry.is_dir.return_value = False
        good_entry.stat.return_value = good_stat

        bad_entry = MagicMock()
        bad_entry.path = str(d / "bad.bin")
        bad_entry.name = "bad.bin"
        bad_entry.is_dir.return_value = False
        bad_entry.stat.side_effect = OSError("permission denied")

        _real_scandir = os.scandir

        def _spy_scandir(path):
            if str(path) == str(d):
                return iter([good_entry, bad_entry])
            return _real_scandir(path)

        monkeypatch.setattr("os.scandir", _spy_scandir)
        assert _dir_size(str(d)) == 100


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 5 — Module does not import or instantiate NSFileCoordinator
# ═══════════════════════════════════════════════════════════════════════════════


class TestModuleNoNSFileCoordinator:
    def test_when_module_is_imported_then_nsfilecoordinator_is_not_in_module_namespace(
        self,
    ):
        assert "NSFileCoordinator" not in vars(_module)

    def test_when_module_is_imported_then_nsfilecoordinator_string_is_not_a_top_level_name(
        self,
    ):
        assert "NSFileCoordinator" not in dir(_module)

    def test_when_pyobjc_trash_is_called_with_spy_foundation_then_nsfilecoordinator_is_never_called(
        self,
    ):
        foundation, _, _ = _mock_foundation()
        with patch.dict(sys.modules, {"Foundation": foundation}):
            _pyobjc_trash("/dummy/path")
        assert not foundation.NSFileCoordinator.called


# ═══════════════════════════════════════════════════════════════════════════════
# Property-based tests
# ═══════════════════════════════════════════════════════════════════════════════


@settings(deadline=None)  # scanning real dirs (e.g. /usr) can exceed 200ms
@given(st.text(min_size=1, max_size=200))
def test_when_file_manager_trasher_trash_is_called_with_any_path_then_it_never_raises(
    path,
):
    """never raises is an absolute invariant over all path inputs."""
    _make_trasher(trash_fn=_noop_trash).trash(path)


@given(st.text(min_size=1, max_size=200))
@settings(deadline=None)
def test_when_file_manager_trasher_trash_fn_fails_for_any_path_then_it_never_raises(
    path,
):
    """never raises holds even when trash_fn always raises."""
    _make_trasher(trash_fn=_failing_trash).trash(path)


@settings(deadline=None)  # scanning real dirs (e.g. /usr) can exceed 200ms
@given(st.text(min_size=1, max_size=200))
def test_when_dir_size_is_called_with_any_path_then_it_never_raises(path):
    """_dir_size is a total function (per-entry skip + missing→0)."""
    _dir_size(path)


@given(st.text(min_size=1, max_size=200).filter(lambda p: not Path(p).exists()))
def test_when_dir_size_is_called_with_nonexistent_path_then_returns_zero(path):
    """missing path → 0 holds for all paths that do not exist."""
    assert _dir_size(path) == 0


@settings(deadline=None)  # scanning real dirs (e.g. /Applications) can exceed 200ms
@given(st.text(min_size=1, max_size=200))
def test_when_file_manager_trasher_succeeds_then_bytes_freed_is_always_nonnegative(
    path,
):
    result = _make_trasher(trash_fn=_noop_trash).trash(path)
    assert result.bytes_freed >= 0


@settings(deadline=None)  # scanning real dirs (e.g. /usr) can exceed 200ms
@given(st.text(min_size=1, max_size=200))
def test_when_file_manager_trasher_fails_then_bytes_freed_is_always_zero(path):
    """On failure bytes_freed is 0 (no size reported for a failed trash)."""
    result = _make_trasher(trash_fn=_failing_trash).trash(path)
    assert result.bytes_freed == 0


@settings(deadline=None)  # scanning real dirs (e.g. /usr) can exceed 200ms
@given(st.text(min_size=1, max_size=200))
def test_when_file_manager_trasher_fails_then_reversibility_is_always_reversible(path):
    """Failure never escalates reversibility to PERMANENT."""
    result = _make_trasher(trash_fn=_failing_trash).trash(path)
    assert result.reversibility is Reversibility.REVERSIBLE
