"""Tests for Issue #42: daemon persists state snapshot to disk.

Acceptance criteria verified:
  AC1 — Daemon writes state_path JSON each tick and on shutdown (atomic, no
        partial writes).
  AC2 — wake_proxies in the snapshot is sourced from wake_manager.active().
  AC3 — sentinel status reflects the live state + active wake proxies produced
        by a running daemon (integration test, no test-written snapshot).
  AC4 — Existing tests still pass (verified by running full suite; no extra test
        needed here beyond checking that build_daemon still works without state_path).

Design choices:
  - state_path=None (default) is a no-op; existing tests call build_daemon without it.
  - _flush_snapshot() is called after all registrations inside _post_tick so that
    active() already reflects newly registered stacks.
  - On shutdown, _flush_snapshot() is called after stop_all() so the persisted
    wake_proxies is empty (matching observable post-daemon state).
  - FakeWakeManager here implements active() as required by the WakeProxyManager
    protocol; existing test fakes omit it because state_path is never set in those
    tests, so active() is never called.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sentinel.config import ServiceConfig
from sentinel.domain.value_objects import (
    DetectionResult,
    DiskUsage,
    MemoryReport,
    PressureLevel,
    PublishedPort,
    SentinelState,
    StackPorts,
    SwapUsage,
)
from sentinel.service.daemon import build_daemon
from sentinel.service.status_provider import DefaultStatusProvider


# ─────────────────────────────────────────────────────────────────────────────
# Fake collaborators
# ─────────────────────────────────────────────────────────────────────────────


class _FakePipeline:
    def __init__(self, state: object = None) -> None:
        self._state = state if state is not None else SentinelState.WARN

    def step(self) -> object:
        return self._state


class _FakeDetect:
    def __call__(self, state: object) -> object:
        return {"candidates": []}


class _FakeAdvisor:
    def rank(self, detection: object) -> list:
        return []


class _FakeEngine:
    def __init__(self, results: list | None = None) -> None:
        self._results = results or []

    def execute(self, candidates: object, state: object) -> list:
        return self._results


class _FakeClock:
    def __init__(self) -> None:
        self._t: float = 0.0

    def monotonic(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._t += seconds


class _FakeWakeManager:
    """WakeProxyManager stub that implements active() per the protocol."""

    def __init__(self, active_stacks: tuple[str, ...] = ()) -> None:
        self._active: list[str] = list(active_stacks)
        self.registered: list = []
        self.stop_all_called: bool = False

    def register(self, registration: object) -> None:
        self.registered.append(registration)
        if hasattr(registration, "stack"):
            self._active.append(registration.stack)

    def active(self) -> tuple[str, ...]:
        return tuple(self._active)

    def stop_all(self) -> None:
        self._active.clear()
        self.stop_all_called = True


class _FakePortDiscoverer:
    def __init__(self, host_port: int = 8080) -> None:
        self._host_port = host_port

    def discover(self, target: str) -> StackPorts:
        return StackPorts(
            stack=target,
            containers=(target,),
            ports=(
                PublishedPort(
                    host_ip="127.0.0.1",
                    host_port=self._host_port,
                    container_port=self._host_port,
                ),
            ),
        )


class _FakeStopResult:
    kind = "STOP_CONTAINER"
    success = True

    def __init__(self, target: str) -> None:
        self.target = target


class _FakeSampler:
    def sample(self) -> object:
        class _S:
            pressure = PressureLevel.WARN
            memory = MemoryReport(
                total_bytes=16_000_000_000,
                used_bytes=8_000_000_000,
                free_bytes=1_000_000_000,
            )
            swap = SwapUsage(
                total_bytes=2_000_000_000,
                used_bytes=512_000_000,
                free_bytes=1_536_000_000,
            )
            disks = (
                DiskUsage(
                    mount="/",
                    free_bytes=25_000_000_000,
                    total_bytes=100_000_000_000,
                ),
            )

        return _S()


class _FakeDetectionService:
    def detect(self, state: object) -> DetectionResult:
        return DetectionResult(processes=(), containers=())


# ─────────────────────────────────────────────────────────────────────────────
# Builder helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_config(**kw: object) -> ServiceConfig:
    defaults: dict = {"interval": 0.0, "min_lifetime": 0.0, "exit_timeout": 5.0}
    defaults.update(kw)
    return ServiceConfig(**defaults)  # type: ignore[arg-type]


def _build(
    *,
    state_path: Path | None = None,
    state: object = None,
    results: list | None = None,
    wake_manager: _FakeWakeManager | None = None,
    port_discoverer: _FakePortDiscoverer | None = None,
    config: ServiceConfig | None = None,
    clock: _FakeClock | None = None,
) -> object:
    clock = clock or _FakeClock()
    return build_daemon(
        config or _make_config(),
        pipeline=_FakePipeline(state=state),
        detect=_FakeDetect(),
        advisor=_FakeAdvisor(),
        engine=_FakeEngine(results=results),
        port_discoverer=port_discoverer or _FakePortDiscoverer(),
        wake_manager=wake_manager or _FakeWakeManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        state_path=state_path,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — file is written each tick
# ─────────────────────────────────────────────────────────────────────────────


def test_when_tick_called_with_state_path_then_snapshot_file_is_created(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path)
    daemon.tick()
    assert state_path.exists(), "state.json must be written after tick()"


def test_when_tick_called_then_snapshot_contains_valid_json_object(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path)
    daemon.tick()
    raw = json.loads(state_path.read_text())
    assert isinstance(raw, dict), "snapshot must be a JSON object"
    assert "state" in raw, "snapshot must contain 'state' key"
    assert "wake_proxies" in raw, "snapshot must contain 'wake_proxies' key"


def test_when_tick_called_then_snapshot_state_reflects_pipeline_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path, state=SentinelState.WARN)
    daemon.tick()
    raw = json.loads(state_path.read_text())
    assert raw["state"] == "warn"


def test_when_tick_called_with_critical_state_then_snapshot_state_is_critical(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path, state=SentinelState.CRITICAL)
    daemon.tick()
    raw = json.loads(state_path.read_text())
    assert raw["state"] == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — atomic write: no .tmp file left behind
# ─────────────────────────────────────────────────────────────────────────────


def test_when_tick_writes_snapshot_then_no_tmp_file_remains(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path)
    daemon.tick()
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"atomic write must not leave .tmp files; found {leftover}"


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — snapshot is written on shutdown
# ─────────────────────────────────────────────────────────────────────────────


def test_when_daemon_stopped_then_snapshot_file_exists(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path)
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    assert state_path.exists(), "state.json must be written on daemon shutdown"


def test_when_daemon_stopped_then_shutdown_snapshot_contains_valid_json(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path)
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    raw = json.loads(state_path.read_text())
    assert isinstance(raw, dict) and "state" in raw and "wake_proxies" in raw


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — no-op when state_path is None (backward compatibility)
# ─────────────────────────────────────────────────────────────────────────────


def test_when_state_path_is_none_then_tick_does_not_raise() -> None:
    daemon = _build(state_path=None)
    daemon.tick()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — wake_proxies sourced from wake_manager.active()
# ─────────────────────────────────────────────────────────────────────────────


def test_when_tick_registers_proxy_then_snapshot_wake_proxies_contains_stack(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    wm = _FakeWakeManager()
    daemon = _build(
        state_path=state_path,
        results=[_FakeStopResult("clipcraft_api")],
        wake_manager=wm,
        port_discoverer=_FakePortDiscoverer(host_port=8080),
    )
    daemon.tick()
    raw = json.loads(state_path.read_text())
    assert "clipcraft_api" in raw["wake_proxies"], (
        "snapshot wake_proxies must include stacks from wake_manager.active()"
    )


def test_when_no_proxy_registered_then_snapshot_wake_proxies_is_empty(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    daemon = _build(state_path=state_path, results=[])
    daemon.tick()
    raw = json.loads(state_path.read_text())
    assert raw["wake_proxies"] == [], (
        "snapshot wake_proxies must be empty when no proxies are registered"
    )


def test_when_shutdown_after_stop_all_then_snapshot_wake_proxies_is_empty(
    tmp_path: Path,
) -> None:
    """stop_all() clears active(); shutdown snapshot must reflect the cleared state."""
    state_path = tmp_path / "state.json"
    wm = _FakeWakeManager()
    daemon = _build(
        state_path=state_path,
        results=[_FakeStopResult("clipcraft_api")],
        wake_manager=wm,
        port_discoverer=_FakePortDiscoverer(host_port=8080),
    )
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    raw = json.loads(state_path.read_text())
    assert raw["wake_proxies"] == [], (
        "shutdown snapshot must have empty wake_proxies — stop_all() was called first"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — status provider reads daemon-written snapshot (no test-written snapshot)
# ─────────────────────────────────────────────────────────────────────────────


def _build_status_provider(
    *,
    snapshot_path: Path,
    audit_log_path: Path,
) -> DefaultStatusProvider:
    return DefaultStatusProvider(
        sampler=_FakeSampler(),
        detection=_FakeDetectionService(),
        snapshot_path=snapshot_path,
        audit_log_path=audit_log_path,
    )


def test_when_daemon_writes_snapshot_then_status_provider_reports_state_without_test_written_snapshot(
    tmp_path: Path,
) -> None:
    """AC3: no call to any helper that writes the snapshot file — daemon owns it."""
    state_path = tmp_path / "state.json"
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("")

    daemon = _build(state_path=state_path, state=SentinelState.WARN)
    daemon.tick()  # daemon writes state.json

    report = _build_status_provider(
        snapshot_path=state_path, audit_log_path=audit_log
    ).build()

    assert report.state == SentinelState.WARN, (
        "StatusReport.state must match the daemon-written snapshot — "
        "no test-written snapshot involved"
    )


def test_when_daemon_registers_proxy_then_status_provider_reports_proxy_without_test_written_snapshot(
    tmp_path: Path,
) -> None:
    """AC3: daemon tick registers proxy → writes snapshot → status provider reads it."""
    state_path = tmp_path / "state.json"
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("")

    wm = _FakeWakeManager()
    daemon = _build(
        state_path=state_path,
        results=[_FakeStopResult("clipcraft_api")],
        wake_manager=wm,
        port_discoverer=_FakePortDiscoverer(host_port=8080),
    )
    daemon.tick()

    report = _build_status_provider(
        snapshot_path=state_path, audit_log_path=audit_log
    ).build()

    assert "clipcraft_api" in report.wake_proxies, (
        "status report wake_proxies must contain the stack registered by the daemon tick "
        "with no test-written snapshot"
    )


def test_when_daemon_runs_and_stops_then_status_provider_can_read_shutdown_snapshot(
    tmp_path: Path,
) -> None:
    """AC3: running daemon (run() in thread) writes snapshot; status provider reads it."""
    state_path = tmp_path / "state.json"
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("")

    daemon = _build(state_path=state_path, state=SentinelState.WARN)
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)

    assert state_path.exists(), "daemon must write snapshot before exiting"

    report = _build_status_provider(
        snapshot_path=state_path, audit_log_path=audit_log
    ).build()

    assert isinstance(report.state, SentinelState), (
        "status report state must be a valid SentinelState read from daemon snapshot"
    )
    assert isinstance(report.wake_proxies, tuple), (
        "status report wake_proxies must be a tuple read from daemon snapshot"
    )
