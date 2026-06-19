"""Trash-mover and permanent-deleter OS shims for Sentinel disk cleanup.

FileManagerTrasher  — reversible-intent path via macOS NSFileManager.trashItem.
OsRemoveDeleter     — permanent-delete path (build artifacts) via os.remove / shutil.rmtree.

PyObjC (Foundation) is imported deferred inside _pyobjc_trash only — importing
this module never requires PyObjC to be installed.  Mirrors the pattern in
sentinel.process.frontmost.NSWorkspaceFrontmostReader.

Design note (reversibility-invariant):
  On trashItem failure FileManagerTrasher returns success=False with
  reversibility=REVERSIBLE.  Silently escalating to permanent delete would
  violate the reversible-by-default invariant on exactly the volumes (external
  drives, Dropbox, Google Drive) that most often reject trashItem.  Callers that
  genuinely need permanent removal should use OsRemoveDeleter directly.
"""

from __future__ import annotations

import os
import shutil
from typing import Callable

from sentinel.domain.value_objects import ActionKind, ActionResult, Reversibility


# ── helpers ────────────────────────────────────────────────────────────────────


def _dir_size(path: str) -> int:
    """Best-effort recursive byte count.  Missing/invalid path → 0; per-entry errors skipped."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return 0
    if not os.path.isdir(path):
        return st.st_size
    total = 0
    for entry in os.scandir(path):
        try:
            if entry.is_dir(follow_symlinks=False):
                total += _dir_size(entry.path)
            else:
                total += entry.stat(follow_symlinks=False).st_size
        except (OSError, ValueError):
            pass
    return total


def _pyobjc_trash(path: str) -> None:
    """Move *path* to the macOS Trash via NSFileManager.trashItemAtURL.

    Foundation is imported here — deferred so importing this module never
    hard-requires PyObjC.  No NSFileCoordinator: wrapping in one causes
    deadlocks / beachballs on Dropbox / Google Drive directories.  [3-0]
    """
    from Foundation import NSFileManager, NSURL  # noqa: PLC0415  # type: ignore[import-untyped]

    url = NSURL.fileURLWithPath_(path)
    ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        url, None, None
    )
    if not ok:
        raise OSError(f"trashItemAtURL failed: {err}")


# ── shim classes ───────────────────────────────────────────────────────────────


class FileManagerTrasher:
    """Reversible file mover via macOS NSFileManager.trashItem.

    On trash failure returns success=False with reversibility=REVERSIBLE so the
    audit log preserves the caller's reversible intent.  Never raises.
    """

    def __init__(self, trash_fn: Callable[[str], None] = _pyobjc_trash) -> None:
        self._trash_fn = trash_fn

    def trash(self, path: str) -> ActionResult:
        size = _dir_size(path)
        try:
            self._trash_fn(path)
            return ActionResult(
                kind=ActionKind.TRASH,
                target=path,
                success=True,
                reversibility=Reversibility.REVERSIBLE,
                bytes_freed=size,
            )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                kind=ActionKind.TRASH,
                target=path,
                success=False,
                reversibility=Reversibility.REVERSIBLE,
                bytes_freed=0,
                detail=f"could not move to Trash: {exc}",
            )


class OsRemoveDeleter:
    """Permanent deleter via os.remove (file) or shutil.rmtree (directory).

    For regenerable build artifacts only — callers must opt in explicitly.
    Nonexistent path → success=False, never raises.
    """

    def __init__(
        self,
        rmtree: Callable[[str], None] = shutil.rmtree,
        remove: Callable[[str], None] = os.remove,
    ) -> None:
        self._rmtree = rmtree
        self._remove = remove

    def delete(self, path: str) -> ActionResult:
        size = _dir_size(path)
        try:
            self._dispatch(path)
            return ActionResult(
                kind=ActionKind.DELETE,
                target=path,
                success=True,
                reversibility=Reversibility.PERMANENT,
                bytes_freed=size,
            )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                kind=ActionKind.DELETE,
                target=path,
                success=False,
                reversibility=Reversibility.PERMANENT,
                bytes_freed=0,
                detail=str(exc),
            )

    def _dispatch(self, path: str) -> None:
        if os.path.isdir(path):
            self._rmtree(path)
        else:
            self._remove(path)
