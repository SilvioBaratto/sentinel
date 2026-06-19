"""
Tests for issue #38: status reporter
(pressure/usage + recent actions + idle candidates + audit tail).

Fakes derived from acceptance criteria; implementations cross-checked against src/.

API contract satisfied by DefaultStatusProvider:

    from sentinel.service.status_provider import DefaultStatusProvider

    DefaultStatusProvider(
        sampler          – protocol: .sample() -> result with:
                            .pressure  PressureLevel (1/2/4)
                            .memory    MemoryReport
                            .swap      SwapUsage
                            .disks     tuple[DiskUsage, ...]
        detection        – protocol: .detect(state) -> result with:
                            .processes   tuple of process candidates
                            .containers  tuple of container candidates
        snapshot_path    – pathlib.Path; JSON file written by daemon:
                            {"state": "warn", "wake_proxies": ["clipcraft", ...]}
                            Missing file → graceful empty defaults (no raise)
        audit_log_path   – pathlib.Path; key=value rotating log from RotatingAuditLogger:
                            target={t} size={sz} reversibility={r} mode={m} success={ok}
                            Missing or empty file → empty recent_actions (no raise)
        tail_n           – int (default 20): max lines read from end of audit log
    )

    .build() -> StatusReport with attributes:
        .pressure          PressureLevel   from sampler
        .state             SentinelState   from snapshot
        .memory            MemoryReport    from sampler (passed through unchanged)
        .swap              SwapUsage       from sampler (passed through unchanged)
        .disks             tuple[DiskUsage, ...]  from sampler
        .idle_processes    tuple           from detection.detect()
        .idle_containers   tuple           from detection.detect()
        .recent_actions    tuple[ActionResult, ...]  from audit log tail
        .wake_proxies      tuple[str, ...]  from snapshot

    ActionResult.reversibility (Reversibility):
        Reversibility.REVERSIBLE → truthy  (bool(x) == True)
        Reversibility.PERMANENT  → falsy   (bool(x) == False)
"""

import json
import pathlib
import string
import tempfile
from dataclasses import dataclass, field

from hypothesis import given, settings, strategies as st

from sentinel.domain.value_objects import (
    DetectionResult,
    DiskUsage,
    MemoryReport,
    PressureLevel,
    SentinelState,
    SwapUsage,
)
from sentinel.service.status_provider import DefaultStatusProvider


# ─── Default metric objects (shared across fakes) ─────────────────────────────


def _mk_memory() -> MemoryReport:
    return MemoryReport(
        total_bytes=16_000_000_000, used_bytes=8_000_000_000, free_bytes=1_000_000_000
    )


def _mk_swap() -> SwapUsage:
    return SwapUsage(
        total_bytes=2_048_000_000, used_bytes=512_000_000, free_bytes=1_536_000_000
    )


def _mk_disks() -> tuple:
    return (
        DiskUsage(mount="/", free_bytes=25_000_000_000, total_bytes=100_000_000_000),
    )


# ─── Fake domain objects ──────────────────────────────────────────────────────


@dataclass
class _FakeSample:
    pressure: PressureLevel = PressureLevel.NORMAL
    memory: MemoryReport = field(default_factory=_mk_memory)
    swap: SwapUsage = field(default_factory=_mk_swap)
    disks: tuple = field(default_factory=_mk_disks)


@dataclass
class _FakeProcess:
    """Minimal duck-typed stand-in for ProcessCandidate."""

    pid: int
    name: str


@dataclass
class _FakeContainer:
    """Minimal duck-typed stand-in for ContainerCandidate."""

    name: str


# ─── Fake protocol implementations ───────────────────────────────────────────


class _Sampler:
    def __init__(self, sample: _FakeSample | None = None) -> None:
        self._sample = sample or _FakeSample()
        self.call_count = 0

    def sample(self) -> _FakeSample:
        self.call_count += 1
        return self._sample


class _FakeDetection:
    """Fake that satisfies .detect(state) -> DetectionResult."""

    def __init__(self, processes: tuple = (), containers: tuple = ()) -> None:
        self._result = DetectionResult(processes=processes, containers=containers)

    def detect(self, state: SentinelState) -> DetectionResult:
        return self._result


# ─── File-writing helpers ─────────────────────────────────────────────────────


def _log_entry(
    target: str = "clipcraft_api",
    reversible: bool = True,
    size: str = "0 B",
) -> str:
    """Produce a key=value audit log line matching RotatingAuditLogger._format()."""
    rev = "reversible" if reversible else "permanent"
    return f"target={target} size={size} reversibility={rev} mode=auto success=True"


def _write_snapshot(
    path: pathlib.Path,
    state: str = "normal",
    stacks: list | None = None,
) -> None:
    path.write_text(json.dumps({"state": state, "wake_proxies": list(stacks or [])}))


def _write_log(path: pathlib.Path, entries: list[str]) -> None:
    path.write_text("\n".join(entries) + ("\n" if entries else ""))


def _provider(
    tmp_path: pathlib.Path,
    *,
    sampler: _Sampler | None = None,
    detection: _FakeDetection | None = None,
    snapshot_state: str = "normal",
    stacks: list | None = None,
    log_entries: list[str] | None = None,
    tail_n: int = 20,
) -> DefaultStatusProvider:
    snap = tmp_path / "snapshot.json"
    log = tmp_path / "audit.log"
    _write_snapshot(snap, snapshot_state, stacks)
    _write_log(log, log_entries if log_entries is not None else [])
    return DefaultStatusProvider(
        sampler=sampler or _Sampler(),
        detection=detection or _FakeDetection(),
        snapshot_path=snap,
        audit_log_path=log,
        tail_n=tail_n,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 1 [UNIT]:
#   DefaultStatusProvider.build() → StatusReport
#   populates PressureLevel + SentinelState, MemoryReport/SwapUsage/DiskUsage
#   from one fresh sample, idle candidates, recent_actions, wake_proxies.
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildPopulatesFromSampler:
    def test_when_sampler_returns_warn_pressure_then_report_pressure_is_warn(
        self, tmp_path
    ):
        sampler = _Sampler(_FakeSample(pressure=PressureLevel.WARN))
        report = _provider(tmp_path, sampler=sampler, snapshot_state="warn").build()
        assert report.pressure == PressureLevel.WARN
        assert report.pressure == 2  # IntEnum: WARN=2

    def test_when_sampler_returns_critical_pressure_then_report_pressure_is_critical(
        self, tmp_path
    ):
        sampler = _Sampler(_FakeSample(pressure=PressureLevel.CRITICAL))
        report = _provider(tmp_path, sampler=sampler, snapshot_state="critical").build()
        assert report.pressure == PressureLevel.CRITICAL
        assert report.pressure == 4  # IntEnum: CRITICAL=4

    def test_when_sampler_returns_normal_pressure_then_report_pressure_is_normal(
        self, tmp_path
    ):
        sampler = _Sampler(_FakeSample(pressure=PressureLevel.NORMAL))
        report = _provider(tmp_path, sampler=sampler).build()
        assert report.pressure == PressureLevel.NORMAL
        assert report.pressure == 1  # IntEnum: NORMAL=1

    def test_when_sampler_returns_metrics_then_report_memory_swap_disks_are_same_objects(
        self, tmp_path
    ):
        memory = MemoryReport(
            total_bytes=16_000_000_000, used_bytes=8_000_000_000, free_bytes=100_000_000
        )
        swap = SwapUsage(
            total_bytes=4_000_000_000,
            used_bytes=2_000_000_000,
            free_bytes=2_000_000_000,
        )
        disk = DiskUsage(
            mount="/", free_bytes=3_000_000_000, total_bytes=500_000_000_000
        )
        sample = _FakeSample(
            pressure=PressureLevel.WARN, memory=memory, swap=swap, disks=(disk,)
        )
        report = _provider(
            tmp_path, sampler=_Sampler(sample), snapshot_state="warn"
        ).build()
        assert report.memory is memory
        assert report.swap is swap
        assert report.disks == (disk,)

    def test_when_build_called_then_sampler_sample_is_invoked_exactly_once(
        self, tmp_path
    ):
        sampler = _Sampler()
        _provider(tmp_path, sampler=sampler).build()
        assert sampler.call_count == 1


class TestBuildPopulatesFromDetection:
    def test_when_detection_returns_processes_then_report_idle_processes_matches(
        self, tmp_path
    ):
        procs = (
            _FakeProcess(pid=101, name="Chrome"),
            _FakeProcess(pid=202, name="Slack"),
        )
        report = _provider(tmp_path, detection=_FakeDetection(processes=procs)).build()
        assert report.idle_processes == procs

    def test_when_detection_returns_containers_then_report_idle_containers_matches(
        self, tmp_path
    ):
        containers = (_FakeContainer("clipcraft_api"), _FakeContainer("dietwise_api"))
        report = _provider(
            tmp_path, detection=_FakeDetection(containers=containers)
        ).build()
        assert report.idle_containers == containers

    def test_when_detection_returns_nothing_then_report_idle_tuples_are_empty(
        self, tmp_path
    ):
        report = _provider(tmp_path, detection=_FakeDetection()).build()
        assert report.idle_processes == ()
        assert report.idle_containers == ()


class TestBuildPopulatesFromSnapshot:
    def test_when_snapshot_has_warn_state_then_report_state_contains_warn(
        self, tmp_path
    ):
        report = _provider(tmp_path, snapshot_state="warn").build()
        assert "WARN" in str(report.state).upper()

    def test_when_snapshot_has_disk_low_state_then_report_state_contains_disk_low(
        self, tmp_path
    ):
        report = _provider(tmp_path, snapshot_state="disk_low").build()
        assert "DISK" in str(report.state).upper() or "LOW" in str(report.state).upper()

    def test_when_snapshot_has_wake_proxy_stacks_then_report_includes_all_of_them(
        self, tmp_path
    ):
        stacks = ["clipcraft", "dietwise", "ital-ia"]
        report = _provider(tmp_path, stacks=stacks).build()
        assert list(report.wake_proxies) == stacks

    def test_when_snapshot_has_no_wake_proxy_stacks_then_report_stacks_is_empty(
        self, tmp_path
    ):
        report = _provider(tmp_path, stacks=[]).build()
        assert list(report.wake_proxies) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 2 [T3]:
#   Reads rotating audit log tail (last N lines);
#   each line parsed into an ActionResult including reversibility.
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuditLogTailParsing:
    def test_when_log_has_five_entries_then_recent_actions_has_five_rows(
        self, tmp_path
    ):
        entries = [_log_entry(target=f"item_{i}") for i in range(5)]
        report = _provider(tmp_path, log_entries=entries, tail_n=20).build()
        assert len(report.recent_actions) == 5

    def test_when_log_lines_exceed_tail_n_then_only_last_tail_n_are_returned(
        self, tmp_path
    ):
        entries = [_log_entry(target=f"item_{i}") for i in range(10)]
        report = _provider(tmp_path, log_entries=entries, tail_n=3).build()
        assert len(report.recent_actions) == 3

    def test_when_log_lines_are_fewer_than_tail_n_then_all_are_returned(self, tmp_path):
        entries = [_log_entry(), _log_entry()]
        report = _provider(tmp_path, log_entries=entries, tail_n=20).build()
        assert len(report.recent_actions) == 2

    def test_when_log_has_exactly_tail_n_lines_then_all_are_returned(self, tmp_path):
        entries = [_log_entry(target=f"item_{i}") for i in range(5)]
        report = _provider(tmp_path, log_entries=entries, tail_n=5).build()
        assert len(report.recent_actions) == 5

    def test_when_log_entry_reversible_is_true_then_action_row_reversibility_is_truthy(
        self, tmp_path
    ):
        report = _provider(tmp_path, log_entries=[_log_entry(reversible=True)]).build()
        assert len(report.recent_actions) == 1
        assert report.recent_actions[0].reversibility

    def test_when_log_entry_reversible_is_false_then_action_row_reversibility_is_falsy(
        self, tmp_path
    ):
        report = _provider(tmp_path, log_entries=[_log_entry(reversible=False)]).build()
        assert len(report.recent_actions) == 1
        assert not report.recent_actions[0].reversibility

    def test_when_log_has_mixed_reversibility_then_each_row_preserves_its_value(
        self, tmp_path
    ):
        entries = [
            _log_entry(target="cache_dir", reversible=True),
            _log_entry(target="node_modules", reversible=False),
            _log_entry(target="downloads", reversible=True),
        ]
        report = _provider(tmp_path, log_entries=entries, tail_n=20).build()
        assert len(report.recent_actions) == 3
        flags = [bool(r.reversibility) for r in report.recent_actions]
        assert flags == [True, False, True]

    def test_when_tail_n_trims_front_then_last_entries_are_all_reversible_not_first(
        self, tmp_path
    ):
        """Tail reads from the END of the file: last tail_n lines, not the first."""
        entries = [
            _log_entry(reversible=False),  # line 0 — dropped
            _log_entry(reversible=False),  # line 1 — dropped
            _log_entry(reversible=True),  # line 2 — kept (last 3)
            _log_entry(reversible=True),  # line 3 — kept
            _log_entry(reversible=True),  # line 4 — kept
        ]
        report = _provider(tmp_path, log_entries=entries, tail_n=3).build()
        assert len(report.recent_actions) == 3
        assert all(row.reversibility for row in report.recent_actions)

    def test_when_each_action_row_is_parsed_then_it_has_a_reversibility_attribute(
        self, tmp_path
    ):
        entries = [_log_entry(reversible=True), _log_entry(reversible=False)]
        report = _provider(tmp_path, log_entries=entries).build()
        for row in report.recent_actions:
            assert hasattr(row, "reversibility")


# ═══════════════════════════════════════════════════════════════════════════════
# Criterion 3 [UNIT]:
#   Missing/empty audit log or missing snapshot →
#   empty-but-valid StatusReport (no raise).
# ═══════════════════════════════════════════════════════════════════════════════


class TestGracefulDegradation:
    def test_when_audit_log_is_missing_then_build_does_not_raise(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=snap,
            audit_log_path=tmp_path / "nonexistent_audit.log",
            tail_n=20,
        )
        report = p.build()
        assert report is not None

    def test_when_audit_log_is_missing_then_recent_actions_is_empty(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=snap,
            audit_log_path=tmp_path / "nonexistent_audit.log",
            tail_n=20,
        )
        assert p.build().recent_actions == ()

    def test_when_audit_log_is_empty_then_build_does_not_raise(self, tmp_path):
        report = _provider(tmp_path, log_entries=[]).build()
        assert report is not None

    def test_when_audit_log_is_empty_then_recent_actions_is_empty(self, tmp_path):
        assert _provider(tmp_path, log_entries=[]).build().recent_actions == ()

    def test_when_snapshot_is_missing_then_build_does_not_raise(self, tmp_path):
        log = tmp_path / "audit.log"
        log.write_text("")
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=tmp_path / "nonexistent_snapshot.json",
            audit_log_path=log,
            tail_n=20,
        )
        report = p.build()
        assert report is not None

    def test_when_snapshot_is_missing_then_wake_proxies_is_empty(self, tmp_path):
        log = tmp_path / "audit.log"
        log.write_text("")
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=tmp_path / "nonexistent_snapshot.json",
            audit_log_path=log,
            tail_n=20,
        )
        assert p.build().wake_proxies == ()

    def test_when_snapshot_is_missing_then_state_defaults_to_normal(self, tmp_path):
        log = tmp_path / "audit.log"
        log.write_text("")
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=tmp_path / "nonexistent_snapshot.json",
            audit_log_path=log,
            tail_n=20,
        )
        assert p.build().state == SentinelState.NORMAL

    def test_when_both_log_and_snapshot_are_missing_then_build_returns_valid_report(
        self, tmp_path
    ):
        p = DefaultStatusProvider(
            sampler=_Sampler(),
            detection=_FakeDetection(),
            snapshot_path=tmp_path / "no_snap.json",
            audit_log_path=tmp_path / "no_log.log",
            tail_n=20,
        )
        report = p.build()
        assert report is not None
        assert report.recent_actions == ()
        assert report.wake_proxies == ()


# ═══════════════════════════════════════════════════════════════════════════════
# Property-based tests (Hypothesis)
# Invariants derived from acceptance criteria.
# ═══════════════════════════════════════════════════════════════════════════════

_SAFE_CHARS = string.ascii_letters + string.digits + "_-"
_safe_text = st.text(alphabet=_SAFE_CHARS, min_size=1, max_size=32)


@given(
    targets=st.lists(_safe_text, min_size=0, max_size=30),
    tail_n=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=50)
def test_when_log_has_n_entries_and_tail_n_is_k_then_recent_actions_count_is_min_of_n_and_k(
    targets: list[str],
    tail_n: int,
) -> None:
    """Invariant (tail semantics): len(recent_actions) == min(len(entries), tail_n)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp)
        entries = [_log_entry(target=t) for t in targets]
        report = _provider(p, log_entries=entries, tail_n=tail_n).build()
        assert len(report.recent_actions) == min(len(entries), tail_n)


@given(reversible=st.booleans())
@settings(max_examples=10)
def test_when_log_entry_has_any_reversible_bool_then_action_row_reversibility_matches(
    reversible: bool,
) -> None:
    """Invariant: bool(row.reversibility) == reversible for any True/False audit log entry."""
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp)
        report = _provider(p, log_entries=[_log_entry(reversible=reversible)]).build()
        assert len(report.recent_actions) == 1
        assert bool(report.recent_actions[0].reversibility) == reversible
