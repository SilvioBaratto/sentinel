"""Source-blind example tests for issue #13.

feat: process protection classifier — TTY/shell-lineage/session walk, protect-on-ambiguity

Tests for DefaultProcessClassifier authored directly from acceptance criteria.
No implementation source was read. All tests are in the Red phase of TDD.

Assumptions (derived from criteria text and project conventions only):
- DefaultProcessClassifier lives in sentinel.process.classifier.
- classify(proc, index) -> ProcessClassification (from sentinel.domain.value_objects).
- ProcessClassification has: pid, name, protection (ProcessProtection), reason (str).
- ProcessProtection.PROTECTED / REAPABLE live in sentinel.domain.value_objects.
- ProcessConfig is in sentinel.config; it holds never_kill_names and reap_names
  (new fields added for this issue) alongside the existing shell_session_markers.
- The classifier accepts a `config` keyword argument of type ProcessConfig.
"""

from __future__ import annotations

from hypothesis import given, strategies as st


# ---------------------------------------------------------------------------
# Lazy imports — source-blind; module must not exist yet (Red phase).
# ---------------------------------------------------------------------------


def _import_classifier():
    from sentinel.process.classifier import DefaultProcessClassifier

    return DefaultProcessClassifier


def _import_value_objects():
    from sentinel.domain import value_objects

    return value_objects


def _import_config():
    import sentinel.config as cfg

    return cfg


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


def _make_default_classifier():
    DefaultProcessClassifier = _import_classifier()
    cfg = _import_config()
    return DefaultProcessClassifier(config=cfg.ProcessConfig())


# ---------------------------------------------------------------------------
# Criterion 2 [UNIT] — interface: classify returns ProcessClassification
# ---------------------------------------------------------------------------


class TestDefaultProcessClassifierInterface:
    """
    Criterion 2 [UNIT]: DefaultProcessClassifier.classify(proc, index) →
    ProcessClassification with PROTECTED/REAPABLE and a human-readable reason.
    """

    def test_when_classifier_imported_then_class_exists(self):
        assert _import_classifier() is not None

    def test_when_classifier_instantiated_then_classify_method_exists(self):
        classifier = _make_default_classifier()
        assert callable(getattr(classifier, "classify", None))

    def test_when_process_classified_then_result_has_protection_attribute(self):
        classifier = _make_default_classifier()
        proc = _make_process(pid=100, ppid=1, name="Slack")
        result = classifier.classify(proc, {1: _launchd()})
        assert hasattr(result, "protection")

    def test_when_process_classified_then_result_has_non_empty_reason(self):
        """Criterion 2: result carries a human-readable reason string."""
        classifier = _make_default_classifier()
        proc = _make_process(pid=100, ppid=1, name="Slack")
        result = classifier.classify(proc, {1: _launchd()})
        assert isinstance(result.reason, str)
        assert result.reason.strip() != ""

    def test_when_process_classified_then_protection_is_valid_enum_member(self):
        vo = _import_value_objects()
        classifier = _make_default_classifier()
        proc = _make_process(pid=100, ppid=1, name="Slack")
        result = classifier.classify(proc, {1: _launchd()})
        assert result.protection in (
            vo.ProcessProtection.PROTECTED,
            vo.ProcessProtection.REAPABLE,
        )

    def test_when_process_classified_then_result_pid_matches_input_pid(self):
        """ProcessClassification.pid must equal the classified process's pid."""
        classifier = _make_default_classifier()
        proc = _make_process(pid=42, ppid=1, name="Slack")
        result = classifier.classify(proc, {1: _launchd()})
        assert result.pid == 42

    def test_when_process_classified_then_result_name_matches_input_name(self):
        """ProcessClassification.name must equal the classified process's name."""
        classifier = _make_default_classifier()
        proc = _make_process(pid=100, ppid=1, name="MyApp")
        result = classifier.classify(proc, {1: _launchd()})
        assert result.name == "MyApp"


# ---------------------------------------------------------------------------
# Criterion 4 [T3] — shell/terminal ancestry → PROTECTED
# ---------------------------------------------------------------------------


class TestClassifierShellLineage:
    """
    Criterion 4 [T3]: child (any depth) of -zsh/login/ssh/tmux/screen via the pid index
    → PROTECTED (explicitly covers python/node/training jobs).
    """

    def _classify_child_of(self, parent_name: str, child_name: str = "python"):
        vo = _import_value_objects()
        shell = _make_process(
            pid=50, ppid=1, name=parent_name, has_tty=True, tty="/dev/ttys001"
        )
        proc = _make_process(pid=100, ppid=50, name=child_name)
        index = {1: _launchd(), 50: shell}
        return _make_default_classifier().classify(proc, index), vo

    def test_when_python_is_child_of_zsh_then_classified_as_protected(self):
        result, vo = self._classify_child_of("-zsh", "python")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_python_is_child_of_login_then_classified_as_protected(self):
        result, vo = self._classify_child_of("login", "python")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_node_is_child_of_ssh_then_classified_as_protected(self):
        result, vo = self._classify_child_of("ssh", "node")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_python_is_child_of_tmux_then_classified_as_protected(self):
        result, vo = self._classify_child_of("tmux", "python")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_python_is_child_of_screen_then_classified_as_protected(self):
        result, vo = self._classify_child_of("screen", "python")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_training_job_is_grandchild_of_zsh_then_classified_as_protected(self):
        """Criterion 4: 'any depth' — two hops from -zsh to the training process."""
        vo = _import_value_objects()
        shell = _make_process(
            pid=50, ppid=1, name="-zsh", has_tty=True, tty="/dev/ttys001"
        )
        intermediate = _make_process(pid=100, ppid=50, name="python")
        proc = _make_process(pid=200, ppid=100, name="training_job")
        index = {1: _launchd(), 50: shell, 100: intermediate}
        result = _make_default_classifier().classify(proc, index)
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_node_is_grandchild_of_login_then_classified_as_protected(self):
        """Criterion 4: explicit coverage for node training jobs via login → -bash."""
        vo = _import_value_objects()
        shell = _make_process(
            pid=50, ppid=1, name="login", has_tty=True, tty="/dev/ttys001"
        )
        intermediate = _make_process(pid=100, ppid=50, name="-bash")
        proc = _make_process(pid=200, ppid=100, name="node")
        index = {1: _launchd(), 50: shell, 100: intermediate}
        result = _make_default_classifier().classify(proc, index)
        assert result.protection is vo.ProcessProtection.PROTECTED


# ---------------------------------------------------------------------------
# Criterion 6 [UNIT] — explicit never-kill names → PROTECTED (wins over reap list)
# ---------------------------------------------------------------------------


class TestClassifierNeverKillNames:
    """
    Criterion 6 [UNIT]: Explicit never-kill name (incl. claude, VPN, Docker Desktop,
    colima) → PROTECTED, even if the same name also appears in the reap allow-list.
    """

    def _classify_named(self, name: str):
        vo = _import_value_objects()
        proc = _make_process(pid=100, ppid=1, name=name)
        index = {1: _launchd()}
        return _make_default_classifier().classify(proc, index), vo

    def test_when_process_named_claude_then_classified_as_protected(self):
        result, vo = self._classify_named("claude")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_process_named_docker_desktop_then_classified_as_protected(self):
        result, vo = self._classify_named("Docker Desktop")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_process_named_colima_then_classified_as_protected(self):
        result, vo = self._classify_named("colima")
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_never_kill_name_also_in_reap_list_then_protected_wins(self):
        """
        Criterion 6: never-kill takes priority unconditionally.
        Construct a ProcessConfig where 'TestApp' appears in both never_kill_names and
        reap_names; the classifier must still return PROTECTED.
        Assumption: ProcessConfig.from_mapping accepts never_kill_names and reap_names
        as frozenset fields (added for this issue).
        """
        vo = _import_value_objects()
        cfg = _import_config()
        DefaultProcessClassifier = _import_classifier()
        config = cfg.ProcessConfig.from_mapping(
            {
                "never_kill_names": frozenset({"TestApp"}),
                "reap_names": frozenset({"TestApp"}),
            }
        )
        classifier = DefaultProcessClassifier(config=config)
        proc = _make_process(pid=100, ppid=1, name="TestApp")
        result = classifier.classify(proc, {1: _launchd()})
        assert result.protection is vo.ProcessProtection.PROTECTED

    def test_when_claude_classified_then_result_has_non_empty_reason(self):
        """Criterion 6 + Criterion 2: never-kill result carries a human-readable reason."""
        result, _ = self._classify_named("claude")
        assert isinstance(result.reason, str)
        assert result.reason.strip() != ""


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestClassifierProperties:
    @given(
        st.sampled_from(
            ["-zsh", "login", "ssh", "tmux", "screen", "-bash", "zsh", "bash"]
        )
    )
    def test_when_process_is_direct_child_of_any_shell_marker_then_always_protected(
        self, shell_name: str
    ):
        """
        Invariant (Criterion 4 T3): for ALL shell marker names, a direct child process
        is always classified as PROTECTED.
        Strategy: st.sampled_from enumerates every marker from the spec.
        """
        vo = _import_value_objects()
        shell = _make_process(
            pid=50, ppid=1, name=shell_name, has_tty=True, tty="/dev/ttys001"
        )
        proc = _make_process(pid=100, ppid=50, name="python")
        index = {1: _launchd(), 50: shell}
        result = _make_default_classifier().classify(proc, index)
        assert result.protection is vo.ProcessProtection.PROTECTED

    @given(st.sampled_from(["claude", "Docker Desktop", "colima"]))
    def test_when_process_has_any_never_kill_name_then_always_protected(
        self, name: str
    ):
        """
        Invariant (Criterion 6 UNIT): named never-kill processes are ALWAYS PROTECTED,
        regardless of TTY state, ancestry, or any other attribute.
        """
        vo = _import_value_objects()
        proc = _make_process(pid=100, ppid=1, name=name)
        result = _make_default_classifier().classify(proc, {1: _launchd()})
        assert result.protection is vo.ProcessProtection.PROTECTED

    @given(st.integers(min_value=2, max_value=12))
    def test_when_shell_descendant_at_any_depth_then_classifier_always_returns_protected(
        self, depth: int
    ):
        """
        Invariant (Criterion 4 T3): classifier returns PROTECTED for any ancestry
        depth leading to a shell marker — not just direct children.
        Strategy: build a chain of `depth` intermediate processes rooted at -zsh.
        """
        vo = _import_value_objects()
        shell = _make_process(
            pid=1, ppid=0, name="-zsh", has_tty=True, tty="/dev/ttys001"
        )
        index: dict = {1: shell}
        current_pid = 1
        for i in range(2, depth + 1):
            p = _make_process(pid=i, ppid=current_pid, name=f"proc_{i}")
            index[i] = p
            current_pid = i
        leaf = _make_process(pid=depth + 100, ppid=current_pid, name="leaf_proc")
        result = _make_default_classifier().classify(leaf, index)
        assert result.protection is vo.ProcessProtection.PROTECTED
