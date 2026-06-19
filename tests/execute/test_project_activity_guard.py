"""
Tests for ProjectActivityGuard — Issue #25.

Source-blind: authored from acceptance criteria only (before any implementation).
No files under src/ were read.  This is the Red phase of TDD.

Acceptance criteria exercised:
  1. ProjectActivityGuard implements ActivityGuard: is_active(project_dir) -> bool
  2. Returns True when a running container maps into project_dir
  3. Returns True on recent git activity (.git / .git/index / HEAD mtime within
     config.git_recent_seconds); False when git is stale
  4. Returns False when no container, no git, no recent file activity (deletable)
  5. Fail-safe: any probe raising → True (skip delete); no exception escapes
  6. git_recent_seconds boundary defined (< vs <=) and tested

Property derived from criterion 2:
  Running container forces True regardless of git recency state.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, strategies as st

from sentinel.domain.protocols import ActivityGuard
from sentinel.execute.activity_guard import ProjectActivityGuard


# ---------------------------------------------------------------------------
# Helpers — built from criteria, not from implementation
# ---------------------------------------------------------------------------


def _config(git_recent_seconds: int = 300) -> MagicMock:
    cfg = MagicMock()
    cfg.git_recent_seconds = git_recent_seconds
    return cfg


def _guard(git_recent_seconds: int = 300) -> ProjectActivityGuard:
    return ProjectActivityGuard(config=_config(git_recent_seconds))


def _running_container_for(host_path: pathlib.Path) -> MagicMock:
    """Mock docker SDK container with a bind-mount at host_path."""
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(host_path),
                "Destination": "/workspace",
            }
        ]
    }
    return container


def _set_mtime(path: pathlib.Path, seconds_ago: float) -> None:
    ts = time.time() - seconds_ago
    os.utime(path, (ts, ts))


def _make_git_dir(project: pathlib.Path, seconds_ago: float = 10.0) -> pathlib.Path:
    """Create a minimal .git directory with a fresh-ish mtime."""
    git = project / ".git"
    git.mkdir(exist_ok=True)
    index = git / "index"
    index.write_bytes(b"")
    head = git / "HEAD"
    head.write_text("ref: refs/heads/main")
    _set_mtime(index, seconds_ago=seconds_ago)
    _set_mtime(head, seconds_ago=seconds_ago)
    _set_mtime(git, seconds_ago=seconds_ago)
    return git


# ---------------------------------------------------------------------------
# 1 — Protocol compliance: is_active(project_dir) -> bool
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_when_guard_is_created_then_it_is_an_instance_of_activity_guard(self):
        """ProjectActivityGuard must satisfy the ActivityGuard protocol."""
        assert isinstance(_guard(), ActivityGuard)

    def test_when_guard_is_created_then_is_active_is_callable(self):
        assert callable(getattr(_guard(), "is_active", None))

    def test_when_is_active_is_called_then_return_value_is_bool(self, tmp_path):
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            result = _guard().is_active(tmp_path)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 2 — Running container maps project_dir → True
# ---------------------------------------------------------------------------


class TestRunningContainerActivity:
    def test_when_running_container_mounts_project_dir_then_is_active_returns_true(
        self, tmp_path
    ):
        container = _running_container_for(tmp_path)
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            assert _guard().is_active(tmp_path) is True

    def test_when_running_container_mounts_a_different_dir_then_project_is_not_active(
        self, tmp_path
    ):
        """Mount on a sibling directory must not mark a different project active."""
        project = tmp_path / "my_project"
        project.mkdir()
        other = tmp_path / "other_project"
        other.mkdir()

        container = _running_container_for(other)
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            # no git, no recent files in project → must be False
            assert _guard().is_active(project) is False


# ---------------------------------------------------------------------------
# 3 — Recent git activity → True; stale git → False
# ---------------------------------------------------------------------------


class TestGitActivity:
    def test_when_git_index_mtime_is_within_threshold_then_is_active_returns_true(
        self, tmp_path
    ):
        git = tmp_path / ".git"
        git.mkdir()
        index = git / "index"
        index.write_bytes(b"")
        _set_mtime(index, seconds_ago=10)  # well within 300 s

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=300).is_active(tmp_path) is True

    def test_when_git_head_mtime_is_within_threshold_then_is_active_returns_true(
        self, tmp_path
    ):
        git = tmp_path / ".git"
        git.mkdir()
        head = git / "HEAD"
        head.write_text("ref: refs/heads/main")
        _set_mtime(head, seconds_ago=10)

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=300).is_active(tmp_path) is True

    def test_when_dot_git_dir_mtime_is_within_threshold_then_is_active_returns_true(
        self, tmp_path
    ):
        git = tmp_path / ".git"
        git.mkdir()
        _set_mtime(git, seconds_ago=10)

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=300).is_active(tmp_path) is True

    def test_when_all_git_markers_are_stale_then_is_active_returns_false(
        self, tmp_path
    ):
        git = tmp_path / ".git"
        git.mkdir()
        for name in ("index", "HEAD"):
            f = git / name
            f.write_bytes(b"")
            _set_mtime(f, seconds_ago=3600)
        _set_mtime(git, seconds_ago=3600)

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=300).is_active(tmp_path) is False


# ---------------------------------------------------------------------------
# 4 — No container, no git, no recent file activity → False (deletable)
# ---------------------------------------------------------------------------


class TestInactiveProject:
    def test_when_no_container_no_git_and_no_recent_files_then_is_active_returns_false(
        self, tmp_path
    ):
        """Empty project directory with no docker activity is considered deletable."""
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard().is_active(tmp_path) is False


# ---------------------------------------------------------------------------
# 5 — Fail-safe: any probe error → True; no exception escapes
# ---------------------------------------------------------------------------


class TestFailSafe:
    def test_when_docker_probe_raises_then_is_active_returns_true(self, tmp_path):
        """Docker daemon down (or any docker error) → fail-safe: skip delete."""
        with patch("docker.from_env", side_effect=Exception("connection refused")):
            assert _guard().is_active(tmp_path) is True

    def test_when_docker_raises_then_no_exception_propagates(self, tmp_path):
        with patch("docker.from_env", side_effect=RuntimeError("daemon unreachable")):
            try:
                _guard().is_active(tmp_path)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"is_active must not raise; got {exc!r}")

    def test_when_stat_raises_oserror_then_is_active_returns_true(self, tmp_path):
        """File-system stat errors → fail-safe: treat as active, skip delete."""
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            with patch("os.stat", side_effect=OSError("permission denied")):
                assert _guard().is_active(tmp_path) is True

    def test_when_stat_raises_then_no_exception_propagates(self, tmp_path):
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            with patch("os.stat", side_effect=PermissionError("access denied")):
                try:
                    _guard().is_active(tmp_path)
                except Exception as exc:  # noqa: BLE001
                    pytest.fail(f"is_active must not raise; got {exc!r}")

    def test_when_containers_list_raises_then_is_active_returns_true(self, tmp_path):
        """Error from containers.list() specifically → fail-safe True."""
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.side_effect = Exception(
                "docker API error"
            )
            assert _guard().is_active(tmp_path) is True


# ---------------------------------------------------------------------------
# 6 — git_recent_seconds boundary: age < threshold is recent; age >= threshold is stale
# ---------------------------------------------------------------------------


class TestGitRecentSecondsBoundary:
    def test_when_git_index_age_is_inside_threshold_then_is_active_returns_true(
        self, tmp_path
    ):
        threshold = 60
        git = tmp_path / ".git"
        git.mkdir()
        index = git / "index"
        index.write_bytes(b"")
        _set_mtime(index, seconds_ago=threshold - 2)  # clearly inside

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=threshold).is_active(tmp_path) is True

    def test_when_git_index_age_exactly_equals_threshold_then_it_is_stale(
        self, tmp_path
    ):
        """
        'Within git_recent_seconds' means age < threshold (strict).
        Age == threshold is at or past the boundary → stale → False.
        """
        threshold = 60
        git = tmp_path / ".git"
        git.mkdir()
        index = git / "index"
        index.write_bytes(b"")
        _set_mtime(index, seconds_ago=float(threshold))
        _set_mtime(git, seconds_ago=float(threshold))

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=threshold).is_active(tmp_path) is False

    def test_when_git_index_age_exceeds_threshold_by_one_second_then_is_stale(
        self, tmp_path
    ):
        threshold = 60
        git = tmp_path / ".git"
        git.mkdir()
        index = git / "index"
        index.write_bytes(b"")
        _set_mtime(index, seconds_ago=threshold + 1)
        _set_mtime(git, seconds_ago=threshold + 1)

        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = []
            assert _guard(git_recent_seconds=threshold).is_active(tmp_path) is False


# ---------------------------------------------------------------------------
# Property: running container forces True regardless of git recency
#
# Invariant derived from: "Returns True when a running container maps into
# project_dir" — this must hold for ALL git activity states.
# ---------------------------------------------------------------------------


@given(
    git_age=st.floats(min_value=0.0, max_value=86400.0, allow_nan=False),
    threshold=st.integers(min_value=1, max_value=3600),
)
def test_when_container_maps_project_dir_then_is_active_is_true_regardless_of_git_age(
    git_age: float, threshold: int
) -> None:
    """
    A running container mounting project_dir must force is_active() to True
    no matter whether git markers are fresh or stale.
    """
    with tempfile.TemporaryDirectory() as tmp:
        project = pathlib.Path(tmp)
        _make_git_dir(project, seconds_ago=git_age)

        container = _running_container_for(project)
        with patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.list.return_value = [container]
            assert _guard(git_recent_seconds=threshold).is_active(project) is True
