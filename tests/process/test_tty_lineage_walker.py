"""Source-blind example tests for issue #13.

feat: process protection classifier — TTY/shell-lineage/session walk, protect-on-ambiguity

Tests for TtyLineageWalker authored directly from acceptance criteria.
No implementation source was read. All tests are in the Red phase of TDD.

Assumptions (derived from criteria text and project conventions only):
- TtyLineageWalker lives in sentinel.process.walker.
- It exposes a walk(proc: ProcessInfo, index: dict[int, ProcessInfo]) -> bool method.
- ProcessInfo is in sentinel.domain.value_objects (fields confirmed from existing tests).
"""

from __future__ import annotations

from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Lazy imports — source-blind; module must not exist yet (Red phase).
# ---------------------------------------------------------------------------


def _import_walker():
    from sentinel.process.walker import TtyLineageWalker

    return TtyLineageWalker


def _import_value_objects():
    from sentinel.domain import value_objects

    return value_objects


# ---------------------------------------------------------------------------
# Fixture helpers built from criteria fields only
# ---------------------------------------------------------------------------


def _make_process(
    pid: int,
    ppid: int,
    name: str,
    *,
    has_tty: bool = False,
    tty: str | None = None,
    pgid: int | None = None,
):
    vo = _import_value_objects()
    return vo.ProcessInfo(
        pid=pid,
        ppid=ppid,
        name=name,
        cmdline=(name,),
        has_tty=has_tty,
        tty=tty,
        pgid=pgid,
        cpu_percent=0.0,
        rss_bytes=0,
        create_time=None,
    )


def _launchd():
    return _make_process(pid=1, ppid=0, name="launchd")


# ---------------------------------------------------------------------------
# Criterion 1 [UNIT] — interface
# ---------------------------------------------------------------------------


class TestTtyLineageWalkerInterface:
    """TtyLineageWalker is importable and exposes a walk() callable."""

    def test_when_walker_imported_then_class_exists(self):
        walker_cls = _import_walker()
        assert walker_cls is not None

    def test_when_walker_instantiated_then_walk_method_exists(self):
        walker = _import_walker()()
        assert callable(getattr(walker, "walk", None))


# ---------------------------------------------------------------------------
# Criterion 1 [UNIT] — controlling TTY
# ---------------------------------------------------------------------------


class TestTtyLineageWalkerTty:
    """Walker returns True/False based on whether the process has a controlling TTY."""

    def test_when_process_has_controlling_tty_then_walk_returns_true(self):
        walker = _import_walker()()
        proc = _make_process(
            pid=100, ppid=1, name="python", has_tty=True, tty="/dev/ttys001"
        )
        index = {1: _launchd()}
        assert walker.walk(proc, index) is True

    def test_when_process_has_no_tty_and_no_shell_ancestor_then_walk_returns_false(
        self,
    ):
        walker = _import_walker()()
        proc = _make_process(pid=100, ppid=1, name="Slack")
        index = {1: _launchd()}
        assert walker.walk(proc, index) is False


# ---------------------------------------------------------------------------
# Criterion 1 [UNIT] + Criterion 4 [T3] — shell/terminal ancestry, any depth
# ---------------------------------------------------------------------------


class TestTtyLineageWalkerAncestry:
    """Walker returns True when any ancestor is a shell/terminal session marker."""

    def test_when_process_is_direct_child_of_zsh_then_walk_returns_true(self):
        walker = _import_walker()()
        shell = _make_process(
            pid=50, ppid=1, name="-zsh", has_tty=True, tty="/dev/ttys001"
        )
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_process_is_direct_child_of_login_then_walk_returns_true(self):
        walker = _import_walker()()
        shell = _make_process(
            pid=50, ppid=1, name="login", has_tty=True, tty="/dev/ttys001"
        )
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_process_is_direct_child_of_ssh_then_walk_returns_true(self):
        walker = _import_walker()()
        shell = _make_process(pid=50, ppid=1, name="ssh")
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_process_is_direct_child_of_tmux_then_walk_returns_true(self):
        walker = _import_walker()()
        shell = _make_process(pid=50, ppid=1, name="tmux")
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_process_is_direct_child_of_screen_then_walk_returns_true(self):
        walker = _import_walker()()
        shell = _make_process(pid=50, ppid=1, name="screen")
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_node_is_grandchild_of_ssh_then_walk_returns_true(self):
        """Criterion 4: 'any depth' — two hops from the shell marker."""
        walker = _import_walker()()
        shell = _make_process(pid=50, ppid=1, name="ssh")
        middle = _make_process(pid=100, ppid=50, name="bash")
        proc = _make_process(pid=200, ppid=100, name="node")
        index = {1: _launchd(), 50: shell, 100: middle}
        assert walker.walk(proc, index) is True

    def test_when_training_job_is_deep_descendant_of_tmux_then_walk_returns_true(self):
        """Criterion 4: 'any depth' — three hops: tmux → -zsh → python → training_job."""
        walker = _import_walker()()
        tmux = _make_process(pid=40, ppid=1, name="tmux")
        zsh = _make_process(
            pid=60, ppid=40, name="-zsh", has_tty=True, tty="/dev/ttys001"
        )
        intermed = _make_process(pid=80, ppid=60, name="python")
        proc = _make_process(pid=200, ppid=80, name="training_job")
        index = {1: _launchd(), 40: tmux, 60: zsh, 80: intermed}
        assert walker.walk(proc, index) is True

    def test_when_ancestry_has_no_shell_marker_then_walk_returns_false(self):
        """Ancestor chain with only launchd and non-shell processes → False."""
        walker = _import_walker()()
        parent = _make_process(pid=50, ppid=1, name="Dock")
        proc = _make_process(pid=100, ppid=50, name="Slack")
        index = {1: _launchd(), 50: parent}
        assert walker.walk(proc, index) is False


# ---------------------------------------------------------------------------
# Criterion 1 [UNIT] — process-group sharing with a shell
# ---------------------------------------------------------------------------


class TestTtyLineageWalkerProcessGroup:
    """Walker returns True when the process shares its pgid with a shell/terminal process."""

    def test_when_process_shares_pgid_with_zsh_then_walk_returns_true(self):
        """
        Criterion 1: 'shares its process-group with one' — pgid=50 contains -zsh;
        another process with the same pgid is considered shell-connected.
        """
        walker = _import_walker()()
        shell = _make_process(
            pid=50, ppid=1, name="-zsh", has_tty=True, tty="/dev/ttys001", pgid=50
        )
        proc = _make_process(pid=200, ppid=1, name="python", pgid=50)
        index = {1: _launchd(), 50: shell}
        assert walker.walk(proc, index) is True

    def test_when_process_shares_pgid_only_with_non_shell_then_walk_returns_false(self):
        """No shell in the shared process-group → False."""
        walker = _import_walker()()
        sibling = _make_process(pid=150, ppid=1, name="Slack", pgid=150)
        proc = _make_process(pid=200, ppid=1, name="python", pgid=150)
        index = {1: _launchd(), 150: sibling}
        assert walker.walk(proc, index) is False


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestTtyLineageWalkerProperties:
    @given(st.text(min_size=1))
    def test_when_process_has_any_non_empty_tty_then_walk_always_returns_true(
        self, tty_value: str
    ):
        """
        Invariant (Criterion 1 UNIT): walk returns True for ANY non-empty tty value.
        A controlling TTY string — regardless of its content — qualifies the process.
        Strategy: st.text(min_size=1) covers all printable and Unicode tty-path strings.
        """
        walker = _import_walker()()
        proc = _make_process(pid=100, ppid=1, name="proc", has_tty=True, tty=tty_value)
        index = {1: _launchd()}
        assert walker.walk(proc, index) is True

    @given(st.integers(min_value=1, max_value=15))
    def test_when_process_is_shell_descendant_at_any_depth_then_walk_returns_true(
        self, depth: int
    ):
        """
        Invariant (Criterion 4 T3): 'child (any depth)' — walk must traverse an
        arbitrarily long ancestry chain to find the shell marker.
        Strategy: build a chain of `depth` intermediate processes above the leaf,
        all rooted at a -zsh process.
        """
        walker = _import_walker()()
        # Chain: -zsh(pid=1) → proc_2 → ... → proc_{depth+1} → leaf
        shell = _make_process(
            pid=1, ppid=0, name="-zsh", has_tty=True, tty="/dev/ttys001"
        )
        index: dict = {1: shell}
        current_pid = 1
        for i in range(2, depth + 2):
            p = _make_process(pid=i, ppid=current_pid, name=f"proc_{i}")
            index[i] = p
            current_pid = i
        leaf = _make_process(pid=current_pid + 100, ppid=current_pid, name="leaf")
        assert walker.walk(leaf, index) is True
