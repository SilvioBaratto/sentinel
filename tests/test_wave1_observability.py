"""Wave 1 — observability regressions.

Each test here pins a defect that let sentinel run for 4d19h taking one failing
action every 30s while reporting nothing. They are deliberately written against
the composition roots (build_executor / _build_daemon), because every one of
these bugs lived in the wiring rather than in an adapter — unit tests over the
adapters passed throughout.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sentinel import fmt
from sentinel.audit_format import decode, encode, render
from sentinel.config import AdvisorConfig, ExecuteConfig, NotifyConfig
from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    AuditRecord,
    ExecutionMode,
    Reversibility,
)


def _record(**kw) -> AuditRecord:
    base = dict(
        timestamp=1234.5,
        kind=ActionKind.TRASH,
        target="/Users/x/Library/Caches",
        success=True,
        reversibility=Reversibility.REVERSIBLE,
        bytes_freed=1_200_000_000,
        mode=ExecutionMode.AUTO,
        detail="",
    )
    base.update(kw)
    return AuditRecord(**base)  # type: ignore[arg-type]


# ── 1.1 the audit sink is wired at the composition root ──────────────────────


class TestAuditSinkIsWired:
    def test_when_config_has_no_path_then_caller_fallback_is_used(
        self, tmp_path: Path
    ) -> None:
        """The regression: audit_log_path defaults to None, so build_executor
        installed a NullHandler and no action was ever recorded."""
        from sentinel.execute import build_executor

        log = tmp_path / "sentinel.audit.jsonl"
        engine = build_executor(ExecuteConfig(), audit_log_path=str(log))
        engine._audit.record(_record())

        assert log.exists(), "audit sink fell back to NullHandler"
        assert decode(log.read_text(encoding="utf-8").strip()) is not None

    def test_when_no_path_is_resolvable_then_it_degrades_to_null_not_crash(
        self,
    ) -> None:
        from sentinel.execute import build_executor

        engine = build_executor(ExecuteConfig(), audit_log_path=None)
        engine._audit.record(_record())  # must not raise

    def test_when_log_dir_is_unwritable_then_it_degrades_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        """A raising handler constructor would crash-loop the LaunchAgent."""
        from sentinel.execute import build_executor

        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        engine = build_executor(
            ExecuteConfig(), audit_log_path=str(blocker / "deep" / "audit.jsonl")
        )
        engine._audit.record(_record())  # must not raise

    def test_when_explicit_config_path_is_set_then_it_wins_over_fallback(
        self, tmp_path: Path
    ) -> None:
        from sentinel.execute import build_executor

        chosen = tmp_path / "chosen.jsonl"
        engine = build_executor(
            ExecuteConfig(audit_log_path=str(chosen)),
            audit_log_path=str(tmp_path / "ignored.jsonl"),
        )
        engine._audit.record(_record())
        assert chosen.exists()
        assert not (tmp_path / "ignored.jsonl").exists()


class TestAuditFormatCarriesTheWholeRecord:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("kind", ActionKind.DELETE),
            ("reversibility", Reversibility.PERMANENT),
            ("mode", ExecutionMode.DRY_RUN),
        ],
    )
    def test_when_record_is_encoded_then_enum_fields_survive(
        self, field: str, value
    ) -> None:
        """kind was dropped entirely, so the reader hardcoded STOP_CONTAINER."""
        payload = json.loads(encode(_record(**{field: value})))
        assert payload[field] == value.value

    def test_when_record_is_encoded_then_timestamp_and_detail_survive(self) -> None:
        """detail carries the NSError explaining *why* an action failed."""
        payload = json.loads(
            encode(_record(success=False, detail="trashItemAtURL failed: denied"))
        )
        assert payload["ts"] == 1234.5
        assert "denied" in payload["detail"]

    def test_when_line_is_decoded_then_kind_is_not_hardcoded(self) -> None:
        parsed = decode(encode(_record(kind=ActionKind.DELETE)))
        assert parsed is not None
        assert parsed.kind is ActionKind.DELETE

    def test_when_legacy_key_value_line_is_read_then_it_still_parses(self) -> None:
        legacy = (
            "target=foo size=1.2 GB reversibility=reversible mode=auto success=True"
        )
        parsed = decode(legacy)
        assert parsed is not None and parsed.target == "foo"

    def test_when_line_is_junk_then_decode_returns_none(self) -> None:
        assert decode("not a record at all") is None
        assert decode("") is None

    def test_when_target_contains_newline_then_rendering_cannot_forge_a_line(
        self,
    ) -> None:
        """render() writes to a terminal, so it needs the same guarantee encode does."""
        out = render(encode(_record(target="a\nFAIL delete /etc")))
        assert len(out.splitlines()) == 1


# ── 1.2 the notifier tells the truth, once ───────────────────────────────────


class _SpyRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> None:
        self.calls.append(args)


def _failed() -> ActionResult:
    return ActionResult(
        kind=ActionKind.TRASH,
        target="/Users/x/Library/Caches",
        success=False,
        reversibility=Reversibility.REVERSIBLE,
        bytes_freed=0,
        detail="trashItemAtURL failed: deny delete",
    )


class TestNotifierTruthfulness:
    def test_when_action_failed_then_message_does_not_read_as_success(self) -> None:
        """The regression: every result rendered as '<target> freed 0 B'."""
        msg = fmt.format_action_message(_failed())
        assert "freed" not in msg.lower()
        assert "FAILED" in msg

    def test_when_action_failed_then_reason_is_included(self) -> None:
        assert "deny delete" in fmt.format_action_message(_failed())

    def test_when_trash_succeeds_then_it_is_not_called_freed(self) -> None:
        """A trash relocates bytes within the volume; it does not free them."""
        ok = ActionResult(
            kind=ActionKind.TRASH,
            target="/x",
            success=True,
            reversibility=Reversibility.REVERSIBLE,
            bytes_freed=1000,
        )
        assert "freed" not in fmt.format_action_message(ok).lower()

    def test_when_same_failure_repeats_then_only_one_banner_is_emitted(self) -> None:
        """30s ticks × an always-failing target = ~1600 banners/day without this."""
        from sentinel.notify.notifier import MacNotifier

        clock = {"t": 0.0}
        spy = _SpyRunner()
        n = MacNotifier(
            os_runner=spy, config=NotifyConfig(), monotonic=lambda: clock["t"]
        )
        for _ in range(120):
            clock["t"] += 30.0
            n.notify(_failed())
        assert len(spy.calls) == 1

    def test_when_outcome_changes_then_it_is_announced_again(self) -> None:
        from sentinel.notify.notifier import MacNotifier

        spy = _SpyRunner()
        n = MacNotifier(os_runner=spy, config=NotifyConfig(), monotonic=lambda: 0.0)
        n.notify(_failed())
        ok = ActionResult(
            kind=ActionKind.TRASH,
            target="/Users/x/Library/Caches",
            success=True,
            reversibility=Reversibility.REVERSIBLE,
        )
        n.notify(ok)
        assert len(spy.calls) == 2

    def test_when_runner_explodes_then_notify_never_propagates(self) -> None:
        from sentinel.notify.notifier import MacNotifier

        def boom(_args: list[str]) -> None:
            raise OSError("osascript missing")

        MacNotifier(os_runner=boom, monotonic=lambda: 0.0).notify(_failed())


# ── 1.3 state is written and reported ────────────────────────────────────────


class TestStateIsObservable:
    def test_when_no_snapshot_exists_then_state_reads_unknown_not_normal(
        self, tmp_path: Path
    ) -> None:
        """The regression: a dead daemon and a healthy idle one both said NORMAL."""
        from sentinel.cli import _state_age, _state_label

        assert _state_label(None, None) == "UNKNOWN"
        assert "no snapshot" in _state_age(None)

    def test_when_snapshot_is_old_then_it_is_reported_stale(self) -> None:
        from sentinel.cli import _state_age

        assert "STALE" in _state_age(4 * 86400.0)

    def test_when_snapshot_is_fresh_then_age_is_shown(self) -> None:
        from sentinel.cli import _state_age

        assert _state_age(12.0) == "as of 12s ago"

    def test_when_daemon_flushes_then_snapshot_lands_at_state_path(
        self, tmp_path: Path
    ) -> None:
        """The regression: cli._build_daemon never passed state_path."""
        from sentinel.service.daemon import SentinelDaemon

        class _Wake:
            def active(self):
                return ()

        state = tmp_path / "state.json"
        daemon = SentinelDaemon(
            pipeline=None,
            detect=lambda s: None,
            advisor=None,
            engine=None,
            port_discoverer=None,
            wake_manager=_Wake(),
            config=None,
            monotonic=lambda: 0.0,
            sleep=lambda s: None,
            state_path=state,
        )
        daemon._flush_snapshot()
        assert json.loads(state.read_text(encoding="utf-8"))["state"] == "normal"


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0s"), (12, "12s"), (90, "1m 30s"), (3660, "1h 1m"), (412_000, "4d 18h")],
    )
    def test_durations_render_compactly(self, seconds: float, expected: str) -> None:
        assert fmt.format_duration(seconds) == expected


# ── 1.4 the advisor kill switch actually kills ───────────────────────────────


class TestAdvisorKillSwitch:
    def test_when_advisor_is_disabled_then_factory_returns_null_advisor(self) -> None:
        """The regression: cli._build_daemon bypassed build_advisor entirely."""
        from sentinel.advisor.ollama import NullAdvisor, build_advisor

        assert isinstance(build_advisor(AdvisorConfig()), NullAdvisor)

    def test_when_there_are_no_candidates_then_no_request_is_issued(self) -> None:
        """Detection returns nothing on ~100% of live ticks; that must not POST."""
        from sentinel.advisor.ollama import OllamaAdvisor

        class _Detection:
            processes = ()
            containers = ()

        def opener(*_a, **_kw):
            raise AssertionError("advisor issued a request with no candidates")

        advisor = OllamaAdvisor(AdvisorConfig(enabled=True), opener=opener)
        assert advisor.rank(_Detection()).ordered_targets == ()

    def test_when_request_is_built_then_streaming_is_disabled(self) -> None:
        """Ollama streams by default; NDJSON makes json.loads fail every time."""
        from sentinel.advisor.ollama import _body

        assert json.loads(_body(AdvisorConfig(), ("a",)))["stream"] is False


# ── composition-root smoke: the daemon the LaunchAgent actually builds ───────


class TestDaemonCompositionWiring:
    def test_when_daemon_is_built_then_audit_and_state_paths_are_wired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards every wiring bug in Wave 1 at once: had this existed, none of
        them could have shipped."""
        import sentinel.cli as cli
        from sentinel.config_store import JsonConfigStore

        monkeypatch.setattr(
            cli, "_build_daemon", cli._build_daemon
        )  # keep the real one
        store = JsonConfigStore(base_dir=tmp_path)
        paths = store.paths()

        import sentinel.config_store as cs

        monkeypatch.setattr(
            cs, "JsonConfigStore", lambda *a, **kw: JsonConfigStore(base_dir=tmp_path)
        )

        daemon = cli._build_daemon()
        assert daemon._state_path is not None
        assert str(daemon._state_path) == paths.state_path
        handlers = daemon._engine._audit._logger.handlers
        assert not any(isinstance(h, logging.NullHandler) for h in handlers)
