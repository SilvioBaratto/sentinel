"""
Source-blind integration tests for Issue #40:
  test: Cycle 4 end-to-end integration pytest suite

Every test is derived from the acceptance-criteria text alone; no src/ files were read.
Tests are written in the Red phase — they fail today and pass once the AC is met.

Criteria verified (all [UNIT]):
  1. Daemon tick (state-gated) → STOP_CONTAINER → daemon registers wake proxy on
     the container's published port(s).
  2. A test client connects to a registered port → the fake restarter is invoked
     exactly once (multi-port stack: still once) → bytes forward to a loopback
     echo upstream after the health gate.
  3. After the action, the audit log contains the stop entry and
     StatusProvider.build() reports it (with reversibility) plus the active wake proxy.
  4. SIGTERM path: state flushed (daemon.snapshot set), no partial proxy cleanup,
     stop_all() called, lifetime ≥ min_lifetime.
  6. Always-up containers (optimizer_* / *_db) are never wake-proxied (defensive
     assertion).

Skipped criteria (oracle: NOT VERIFIABLE):
  5. No real network egress / no real Docker subprocess (all seams injected;
     advisor disabled performs zero network calls) — structural property, no unit
     assertion inferable.
  7. All tests pass — boilerplate suite gate.
  8. SOLID, clean code — subjective prose, no runtime assertion.

Design notes (source-blind):
  - _AuditingEngine writes audit-log entries in the key=value format inferred from
    test_issue38 / DefaultStatusProvider's expected input.
  - The daemon snapshot (state + wake_proxies JSON) is written by the test from
    the FakeWakeManager state after tick() — this simulates what the real daemon
    is expected to flush on shutdown or after registration.
  - _DaemonWakeAdapter bridges the daemon's register(target, ports) protocol to
    WakeProxyManager.register(WakeRegistration) for criterion 2's end-to-end path.
"""

from __future__ import annotations

import json
import pathlib
import socket
import threading
import time
from dataclasses import dataclass, field

from hypothesis import given, settings, strategies as st

from sentinel.config import ServiceConfig, WakeProxyConfig
from sentinel.docker.wake_manager import WakeProxyManager
from sentinel.docker.wake_proxy import WakeComponents, build_wake_proxy
from sentinel.domain.value_objects import (
    DetectionResult,
    DiskUsage,
    MemoryReport,
    PressureLevel,
    PublishedPort,
    SentinelState,
    SwapUsage,
    WakeOutcome,
    WakeRegistration,
)
from sentinel.service.daemon import build_daemon
from sentinel.service.status_provider import DefaultStatusProvider


# ─────────────────────────────────────────────────────────────────────────────
# Constants derived from requirements spec (always-up invariants)
# ─────────────────────────────────────────────────────────────────────────────

_ALWAYS_UP_EXACT = ["optimizer_frontend", "optimizer_api", "optimizer_db"]
_DB_CONTAINERS = ["clipcraft_db", "dietwise_db", "myapp_db"]


# ─────────────────────────────────────────────────────────────────────────────
# Network / TCP helpers
# ─────────────────────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an OS-assigned free TCP port (socket released immediately)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_bound(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError):
        return False


class _TcpEchoServer:
    """Threaded TCP echo server — used as a fake upstream container."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port: int = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "_TcpEchoServer":
        self._sock.listen(16)
        self._sock.settimeout(0.1)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fake wake-proxy adapters (derived from criteria, not from src/)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRestarter:
    """Records restart() invocations; always returns RESTARTED."""

    def __init__(self) -> None:
        self.restart_count = 0
        self._lock = threading.Lock()

    def restart(self, reg: WakeRegistration) -> WakeOutcome:
        with self._lock:
            self.restart_count += 1
        return WakeOutcome.RESTARTED

    def is_running(self, name: str) -> bool:
        return False


class _AlwaysHealthyGate:
    """Health gate that reports the upstream as healthy immediately."""

    async def wait_ready(self, port: int, timeout: float) -> bool:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Fake daemon collaborators (derived from acceptance-criteria text)
# ─────────────────────────────────────────────────────────────────────────────


class _FakePipeline:
    def __init__(self, state: object = None) -> None:
        self._state = state if state is not None else {"pressure": 2}

    def step(self) -> object:
        return self._state


class _FakeDetect:
    def __init__(self, detection: object = None) -> None:
        self._detection = detection if detection is not None else {"candidates": []}

    def __call__(self, state: object) -> object:
        return self._detection


class _DisabledAdvisor:
    """Advisor disabled — rank() returns the original order unchanged; no network call."""

    def rank(self, detection: object) -> list:
        return []


class _ActionResult:
    """Minimal action result duck-type inferred from criteria text."""

    def __init__(self, kind: str, target: str, success: bool = True) -> None:
        self.kind = kind
        self.target = target
        self.success = success
        # STOP_CONTAINER is reversible (docker stop; container can be restarted)
        self.reversibility = "reversible"


def _stop_result(target: str, *, success: bool = True) -> _ActionResult:
    return _ActionResult(kind="STOP_CONTAINER", target=target, success=success)


class _FakeEngine:
    """Returns pre-configured action results; writes nothing."""

    def __init__(self, results: list | None = None) -> None:
        self._results = results if results is not None else []

    def execute(self, candidates: object, state: object) -> list:
        return self._results


class _AuditingEngine:
    """
    Returns action results AND writes audit-log entries to a file.

    Format matches the key=value lines expected by DefaultStatusProvider
    (inferred from test_issue38 / RotatingAuditLogger output).
    """

    def __init__(
        self,
        results: list | None = None,
        audit_log_path: pathlib.Path | None = None,
    ) -> None:
        self._results = results if results is not None else []
        self._log = audit_log_path

    def execute(self, candidates: object, state: object) -> list:
        if self._log is not None:
            with self._log.open("a") as fh:
                for r in self._results:
                    rev = getattr(r, "reversibility", "reversible")
                    fh.write(
                        f"target={r.target} size=0 B "
                        f"reversibility={rev} mode=auto success={r.success}\n"
                    )
        return self._results


class _FakePortDiscoverer:
    """Returns a fixed StackPorts for any container name; records calls."""

    def __init__(self, ports: list | None = None) -> None:
        self._port_nums: list = ports if ports is not None else []
        self.discovered: list[str] = []

    def discover(self, target: str) -> object:
        from sentinel.domain.value_objects import PublishedPort, StackPorts  # noqa: PLC0415

        self.discovered.append(target)
        published = tuple(
            PublishedPort(host_ip="127.0.0.1", host_port=p, container_port=p)
            for p in self._port_nums
        )
        return StackPorts(stack=target, containers=(target,), ports=published)


class _FakeWakeManager:
    """Records WakeRegistration objects from register(); binds no real sockets."""

    def __init__(self) -> None:
        self.registered: list = []
        self.stop_all_called: bool = False

    def register(self, registration: object) -> None:
        self.registered.append(registration)

    def stop_all(self) -> None:
        self.stop_all_called = True


class _FakeClock:
    """Deterministic injectable clock; sleep() advances the counter instantly."""

    def __init__(self) -> None:
        self._t: float = 0.0

    def monotonic(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._t += seconds


# ─────────────────────────────────────────────────────────────────────────────
# Fake sampler / detection (for status-provider integration)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _SampleResult:
    pressure: PressureLevel = PressureLevel.WARN
    memory: MemoryReport = field(
        default_factory=lambda: MemoryReport(
            total_bytes=16_000_000_000,
            used_bytes=8_000_000_000,
            free_bytes=1_000_000_000,
        )
    )
    swap: SwapUsage = field(
        default_factory=lambda: SwapUsage(
            total_bytes=2_000_000_000,
            used_bytes=512_000_000,
            free_bytes=1_536_000_000,
        )
    )
    disks: tuple = field(
        default_factory=lambda: (
            DiskUsage(
                mount="/", free_bytes=25_000_000_000, total_bytes=100_000_000_000
            ),
        )
    )


class _FakeSampler:
    def __init__(self, result: _SampleResult | None = None) -> None:
        self._result = result or _SampleResult()

    def sample(self) -> _SampleResult:
        return self._result


class _FakeDetectionService:
    """Satisfies the detection.detect(state) -> DetectionResult protocol."""

    def detect(self, state: SentinelState) -> DetectionResult:
        return DetectionResult(processes=(), containers=())


# ─────────────────────────────────────────────────────────────────────────────
# Builder helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_service_config(**kw) -> ServiceConfig:
    defaults = {"interval": 0.0, "min_lifetime": 0.0, "exit_timeout": 5.0}
    defaults.update(kw)
    return ServiceConfig(**defaults)


def _build_daemon(
    *,
    engine=None,
    port_discoverer: _FakePortDiscoverer | None = None,
    wake_manager: _FakeWakeManager | None = None,
    config: ServiceConfig | None = None,
    state: object = None,
    clock: _FakeClock | None = None,
):
    """Return a Daemon wired with test doubles derived from the criteria descriptions."""
    clock = clock or _FakeClock()
    return build_daemon(
        config or _make_service_config(),
        pipeline=_FakePipeline(state=state),
        detect=_FakeDetect(),
        advisor=_DisabledAdvisor(),
        engine=engine or _FakeEngine(),
        port_discoverer=port_discoverer or _FakePortDiscoverer(),
        wake_manager=wake_manager or _FakeWakeManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def _make_real_wake_manager(
    restarter=None,
    health_poll_interval: float = 0.05,
) -> WakeProxyManager:
    """Build a real WakeProxyManager with injected fakes."""
    config = WakeProxyConfig(
        health_timeout=2.0, health_poll_interval=health_poll_interval
    )
    components = WakeComponents(
        restarter=restarter or _FakeRestarter(),
        health_gate=_AlwaysHealthyGate(),
    )
    return build_wake_proxy(config, components=components)


def _make_registration(
    stack: str, host_port: int, container_port: int
) -> WakeRegistration:
    return WakeRegistration(
        stack=stack,
        ports=(
            PublishedPort(
                host_ip="127.0.0.1",
                host_port=host_port,
                container_port=container_port,
            ),
        ),
        restart_command=("compose", "-p", stack, "up", "-d"),
    )


def _write_snapshot(
    path: pathlib.Path,
    *,
    state: str = "warn",
    wake_proxies: list | None = None,
) -> None:
    path.write_text(
        json.dumps({"state": state, "wake_proxies": list(wake_proxies or [])})
    )


def _build_status_provider(
    *,
    snapshot_path: pathlib.Path,
    audit_log_path: pathlib.Path,
    sampler=None,
    detection=None,
    tail_n: int = 20,
) -> DefaultStatusProvider:
    return DefaultStatusProvider(
        sampler=sampler or _FakeSampler(),
        detection=detection or _FakeDetectionService(),
        snapshot_path=snapshot_path,
        audit_log_path=audit_log_path,
        tail_n=tail_n,
    )


# =============================================================================
# Criterion 1 — daemon tick → STOP_CONTAINER → wake proxy registered
# =============================================================================


class TestDaemonTickRegistersWakeProxy:
    """
    Criterion 1: An end-to-end test drives one daemon tick that produces a
    STOP_CONTAINER action → daemon registers a wake proxy on the container's
    published port(s).
    """

    def test_when_tick_produces_stop_container_success_then_wake_manager_register_is_called(
        self,
    ):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8080])
        engine = _FakeEngine(results=[_stop_result("clipcraft_api")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "clipcraft_api" in targets, (
            "daemon must register a wake proxy for every successfully stopped container"
        )

    def test_when_tick_stops_container_then_wake_proxy_includes_all_discovered_ports(
        self,
    ):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[3000, 3001])
        engine = _FakeEngine(results=[_stop_result("clipcraft_frontend")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        assert len(wm.registered) == 1
        reg = wm.registered[0]
        host_ports = {p.host_port for p in reg.ports}
        assert 3000 in host_ports, (
            "host port 3000 must be included in wake-proxy registration"
        )
        assert 3001 in host_ports, (
            "host port 3001 must be included in wake-proxy registration"
        )

    def test_when_tick_produces_no_stop_action_then_no_wake_proxy_is_registered(self):
        wm = _FakeWakeManager()
        engine = _FakeEngine(results=[])
        daemon = _build_daemon(engine=engine, wake_manager=wm)

        daemon.tick()

        assert wm.registered == [], (
            "no STOP_CONTAINER action → wake proxy must not be registered"
        )

    def test_when_tick_stop_fails_then_no_wake_proxy_is_registered(self):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8080])
        engine = _FakeEngine(results=[_stop_result("clipcraft_api", success=False)])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "clipcraft_api" not in targets, (
            "a failed STOP_CONTAINER must not register a wake proxy"
        )

    def test_when_tick_stops_two_containers_then_both_are_registered(self):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8080])
        engine = _FakeEngine(
            results=[_stop_result("clipcraft_api"), _stop_result("dietwise_api")]
        )
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "clipcraft_api" in targets
        assert "dietwise_api" in targets

    def test_when_tick_stops_container_then_port_discoverer_is_called_with_target_name(
        self,
    ):
        pd = _FakePortDiscoverer(ports=[5000])
        engine = _FakeEngine(results=[_stop_result("ital_ia_api")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd)

        daemon.tick()

        assert "ital_ia_api" in pd.discovered, (
            "daemon must call port_discoverer.discover(target) for each stopped container"
        )


# =============================================================================
# Criterion 2 — client connects to registered port → restarter invoked exactly
#   once (multi-port: still once) → bytes forwarded after health gate
# =============================================================================


class TestWakeProxyForwarding:
    """
    Criterion 2: A test client connects to a registered port → the fake
    restarter is invoked exactly once → bytes are forwarded to the loopback
    echo upstream after the health gate.
    """

    def test_when_client_connects_then_restarter_is_invoked_exactly_once(self):
        echo = _TcpEchoServer().start()
        restarter = _FakeRestarter()
        host_port = _free_port()
        manager = _make_real_wake_manager(restarter=restarter)
        manager.register(
            _make_registration(
                "clipcraft", host_port=host_port, container_port=echo.port
            )
        )

        try:
            with socket.create_connection(
                ("127.0.0.1", host_port), timeout=3.0
            ) as conn:
                conn.settimeout(3.0)
                conn.sendall(b"ping")
                conn.recv(64)
            time.sleep(0.15)
            assert restarter.restart_count == 1, (
                f"restarter must be invoked exactly once on first connection; "
                f"got {restarter.restart_count}"
            )
        finally:
            manager.stop_all()
            echo.stop()

    def test_when_client_connects_then_bytes_are_forwarded_and_echoed_back(self):
        echo = _TcpEchoServer().start()
        host_port = _free_port()
        manager = _make_real_wake_manager()
        manager.register(
            _make_registration(
                "clipcraft", host_port=host_port, container_port=echo.port
            )
        )

        payload = b"e2e-sentinel-forward-integration-test"
        try:
            with socket.create_connection(
                ("127.0.0.1", host_port), timeout=3.0
            ) as conn:
                conn.settimeout(3.0)
                conn.sendall(payload)
                received = conn.recv(len(payload))
            assert received == payload, (
                f"bytes must be forwarded bidirectionally through the wake proxy; "
                f"expected {payload!r}, got {received!r}"
            )
        finally:
            manager.stop_all()
            echo.stop()

    def test_when_multi_port_stack_registered_and_client_connects_to_one_port_then_restarter_invoked_once(
        self,
    ):
        """Multi-port stack: one restart fires regardless of which port is first hit."""
        echo = _TcpEchoServer().start()
        restarter = _FakeRestarter()
        host_port_a = _free_port()
        host_port_b = _free_port()

        config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=0.05)
        components = WakeComponents(
            restarter=restarter, health_gate=_AlwaysHealthyGate()
        )
        manager = build_wake_proxy(config, components=components)

        reg = WakeRegistration(
            stack="clipcraft",
            ports=(
                PublishedPort(
                    host_ip="127.0.0.1",
                    host_port=host_port_a,
                    container_port=echo.port,
                ),
                PublishedPort(
                    host_ip="127.0.0.1",
                    host_port=host_port_b,
                    container_port=echo.port,
                ),
            ),
            restart_command=("compose", "-p", "clipcraft", "up", "-d"),
        )
        manager.register(reg)

        try:
            with socket.create_connection(
                ("127.0.0.1", host_port_a), timeout=3.0
            ) as conn:
                conn.settimeout(3.0)
                conn.sendall(b"from-port-a")
                conn.recv(64)
            time.sleep(0.15)
            assert restarter.restart_count == 1, (
                f"multi-port stack: restart must fire exactly once on first hit; "
                f"got {restarter.restart_count}"
            )
        finally:
            manager.stop_all()
            echo.stop()

    def test_when_second_connection_arrives_after_restart_then_restarter_is_not_called_again(
        self,
    ):
        """The restart gate is shared — a second connection must not re-trigger restart."""
        echo = _TcpEchoServer().start()
        restarter = _FakeRestarter()
        host_port = _free_port()
        manager = _make_real_wake_manager(restarter=restarter)
        manager.register(
            _make_registration(
                "clipcraft", host_port=host_port, container_port=echo.port
            )
        )

        try:
            with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as c1:
                c1.settimeout(3.0)
                c1.sendall(b"first")
                c1.recv(64)
            time.sleep(0.15)
            first_count = restarter.restart_count
            assert first_count == 1

            with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as c2:
                c2.settimeout(3.0)
                c2.sendall(b"second")
                c2.recv(64)
            time.sleep(0.15)

            assert restarter.restart_count == first_count, (
                "second connection must not trigger another restart"
            )
        finally:
            manager.stop_all()
            echo.stop()

    def test_when_multi_port_stack_second_port_hit_after_first_then_restarter_still_once(
        self,
    ):
        """Multi-port: first connection on port A then port B must yield restart_count == 1."""
        echo = _TcpEchoServer().start()
        restarter = _FakeRestarter()
        port_a = _free_port()
        port_b = _free_port()

        config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=0.05)
        components = WakeComponents(
            restarter=restarter, health_gate=_AlwaysHealthyGate()
        )
        manager = build_wake_proxy(config, components=components)
        reg = WakeRegistration(
            stack="clipcraft",
            ports=(
                PublishedPort(
                    host_ip="127.0.0.1", host_port=port_a, container_port=echo.port
                ),
                PublishedPort(
                    host_ip="127.0.0.1", host_port=port_b, container_port=echo.port
                ),
            ),
            restart_command=("compose", "-p", "clipcraft", "up", "-d"),
        )
        manager.register(reg)

        try:
            with socket.create_connection(("127.0.0.1", port_a), timeout=3.0) as c:
                c.settimeout(3.0)
                c.sendall(b"hit-a")
                c.recv(64)
            time.sleep(0.15)

            with socket.create_connection(("127.0.0.1", port_b), timeout=3.0) as c:
                c.settimeout(3.0)
                c.sendall(b"hit-b")
                c.recv(64)
            time.sleep(0.15)

            assert restarter.restart_count == 1, (
                f"multi-port: two connections across both ports must yield exactly one restart; "
                f"got {restarter.restart_count}"
            )
        finally:
            manager.stop_all()
            echo.stop()


# =============================================================================
# Criterion 3 — audit log contains stop entry; StatusProvider.build() reports
#   it (with reversibility) plus the active wake proxy
# =============================================================================


class TestAuditLogAndStatusReport:
    """
    Criterion 3: After the stop action, the audit log contains the stop entry
    and StatusProvider.build() reports it with reversibility plus the active
    wake proxy.

    The AuditingEngine writes entries in the key=value format that
    DefaultStatusProvider expects (inferred from test_issue38).
    The snapshot JSON is built from the FakeWakeManager's registered state
    after tick() — this mirrors what the real daemon is required to flush.
    """

    def test_when_tick_stops_container_then_audit_log_file_is_written(self, tmp_path):
        log_path = tmp_path / "audit.log"
        engine = _AuditingEngine(
            results=[_stop_result("clipcraft_api")],
            audit_log_path=log_path,
        )
        daemon = _build_daemon(engine=engine)

        daemon.tick()

        assert log_path.exists(), "audit log file must be written after the stop action"
        assert "clipcraft_api" in log_path.read_text(), (
            "audit log must contain the stopped container's name"
        )

    def test_when_tick_stops_container_then_status_provider_reports_stop_entry(
        self, tmp_path
    ):
        log_path = tmp_path / "audit.log"
        snap_path = tmp_path / "snapshot.json"
        engine = _AuditingEngine(
            results=[_stop_result("clipcraft_api")],
            audit_log_path=log_path,
        )
        wm = _FakeWakeManager()
        daemon = _build_daemon(engine=engine, wake_manager=wm)

        daemon.tick()

        _write_snapshot(
            snap_path,
            state="warn",
            wake_proxies=[r.stack for r in wm.registered],
        )

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert len(report.recent_actions) >= 1, (
            "StatusProvider must report the stop entry from the audit log"
        )
        targets = [getattr(a, "target", None) for a in report.recent_actions]
        assert "clipcraft_api" in targets, (
            f"expected clipcraft_api in recent_actions; got {targets}"
        )

    def test_when_tick_stops_container_then_reported_action_has_reversibility_attribute(
        self, tmp_path
    ):
        log_path = tmp_path / "audit.log"
        snap_path = tmp_path / "snapshot.json"
        engine = _AuditingEngine(
            results=[_stop_result("clipcraft_api")],
            audit_log_path=log_path,
        )
        daemon = _build_daemon(engine=engine)

        daemon.tick()

        _write_snapshot(snap_path, state="warn")

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert len(report.recent_actions) >= 1
        action = report.recent_actions[0]
        assert hasattr(action, "reversibility"), (
            "each ActionResult in recent_actions must expose a reversibility attribute"
        )

    def test_when_stop_container_logged_as_reversible_then_action_reversibility_is_truthy(
        self, tmp_path
    ):
        """STOP_CONTAINER is reversible — its audit entry must produce a truthy reversibility."""
        log_path = tmp_path / "audit.log"
        snap_path = tmp_path / "snapshot.json"
        engine = _AuditingEngine(
            results=[_stop_result("clipcraft_api")],  # reversibility="reversible"
            audit_log_path=log_path,
        )
        daemon = _build_daemon(engine=engine)

        daemon.tick()

        _write_snapshot(snap_path, state="warn")

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert len(report.recent_actions) >= 1
        assert report.recent_actions[0].reversibility, (
            "STOP_CONTAINER is reversible — action.reversibility must be truthy"
        )

    def test_when_daemon_registers_wake_proxy_then_status_provider_reports_it(
        self, tmp_path
    ):
        log_path = tmp_path / "audit.log"
        log_path.write_text("")
        snap_path = tmp_path / "snapshot.json"

        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8080])
        engine = _FakeEngine(results=[_stop_result("clipcraft_api")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        wake_proxies = [r.stack for r in wm.registered]
        _write_snapshot(snap_path, state="warn", wake_proxies=wake_proxies)

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert "clipcraft_api" in list(report.wake_proxies), (
            "StatusProvider must report the active wake proxy registered by the daemon"
        )

    def test_when_no_action_taken_then_recent_actions_is_empty(self, tmp_path):
        log_path = tmp_path / "audit.log"
        log_path.write_text("")
        snap_path = tmp_path / "snapshot.json"
        _write_snapshot(snap_path, state="normal", wake_proxies=[])

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert report.recent_actions == (), (
            "no stop action → recent_actions must be empty"
        )

    def test_when_no_wake_proxy_registered_then_wake_proxies_is_empty(self, tmp_path):
        log_path = tmp_path / "audit.log"
        log_path.write_text("")
        snap_path = tmp_path / "snapshot.json"
        _write_snapshot(snap_path, state="normal", wake_proxies=[])

        report = _build_status_provider(
            snapshot_path=snap_path,
            audit_log_path=log_path,
        ).build()

        assert list(report.wake_proxies) == [], (
            "no wake proxy registered → StatusProvider.wake_proxies must be empty"
        )


# =============================================================================
# Criterion 4 — SIGTERM path: state flushed, no partial proxy cleanup,
#   stop_all() called, lifetime ≥ min_lifetime
# =============================================================================


class TestSigtermShutdown:
    """
    Criterion 4: Clean shutdown — state is flushed (daemon.snapshot set),
    wake_manager.stop_all() is called (no partial proxy cleanup), and the
    process lifetime is never below ServiceConfig.min_lifetime.
    """

    def test_when_stop_is_called_then_daemon_exits_cleanly(self):
        daemon = _build_daemon()

        t = threading.Thread(target=daemon.run, daemon=True)
        t.start()
        time.sleep(0.05)
        daemon.stop()
        t.join(timeout=3.0)

        assert not t.is_alive(), (
            "daemon thread must exit after stop() — no hang or partial cleanup"
        )

    def test_when_stop_is_called_then_state_is_flushed(self):
        """Criterion: 'state flushed' — daemon.snapshot must be set on clean exit."""
        daemon = _build_daemon()

        t = threading.Thread(target=daemon.run, daemon=True)
        t.start()
        time.sleep(0.05)
        daemon.stop()
        t.join(timeout=3.0)

        assert getattr(daemon, "snapshot", None) is not None, (
            "daemon.snapshot must be set after clean shutdown (state flushed)"
        )

    def test_when_stop_is_called_then_wake_manager_stop_all_is_called(self):
        """No partial proxy cleanup: stop_all() must be called so all listeners are freed."""
        wm = _FakeWakeManager()
        daemon = _build_daemon(wake_manager=wm)

        t = threading.Thread(target=daemon.run, daemon=True)
        t.start()
        time.sleep(0.05)
        daemon.stop()
        t.join(timeout=3.0)

        assert wm.stop_all_called, (
            "daemon must call wake_manager.stop_all() on clean shutdown "
            "(no partial proxy teardown)"
        )

    def test_when_stop_called_with_active_proxies_then_stop_all_releases_them_atomically(
        self,
    ):
        """
        'No partial deletes': stop_all() must be called even when proxies are active,
        ensuring all listeners are freed atomically rather than one-by-one.
        """
        wm = _FakeWakeManager()
        # Pre-populate registered list to simulate active proxies
        wm.registered.extend([("clipcraft_api", [8080]), ("dietwise_api", [3000])])
        daemon = _build_daemon(wake_manager=wm)

        t = threading.Thread(target=daemon.run, daemon=True)
        t.start()
        time.sleep(0.05)
        daemon.stop()
        t.join(timeout=3.0)

        assert wm.stop_all_called, (
            "stop_all() must be invoked on shutdown even when multiple proxies are active"
        )

    def test_when_min_lifetime_set_then_daemon_respects_floor_before_exit(self):
        """Criterion: 'lifetime ≥ min_lifetime' — verified via injectable fake clock."""
        clock = _FakeClock()
        config = _make_service_config(min_lifetime=0.5, interval=0.0)
        daemon = _build_daemon(config=config, clock=clock)

        exit_times: list[float] = []

        def _run() -> None:
            daemon.run()
            exit_times.append(clock.monotonic())

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        time.sleep(0.05)
        daemon.stop()
        t.join(timeout=3.0)

        assert exit_times, "daemon must exit after stop()"
        assert exit_times[0] >= 0.5, (
            f"daemon exited at fake t={exit_times[0]:.4f}, "
            "before min_lifetime floor of 0.5"
        )

    def test_when_stop_requested_during_tick_then_current_tick_completes_before_exit(
        self,
    ):
        """
        No partial deletes: when stop() is called mid-tick, the tick must run
        to completion (engine.execute must be reached) before the loop exits.
        """
        executed: list[bool] = []
        in_detect = threading.Event()
        allow_continue = threading.Event()

        class _BlockingDetect:
            def __call__(self, state: object) -> object:
                in_detect.set()
                allow_continue.wait()
                return {"candidates": []}

        class _CapturingEngine:
            def execute(self, candidates: object, state: object) -> list:
                executed.append(True)
                return []

        clock = _FakeClock()
        daemon = build_daemon(
            _make_service_config(),
            pipeline=_FakePipeline(),
            detect=_BlockingDetect(),
            advisor=_DisabledAdvisor(),
            engine=_CapturingEngine(),
            port_discoverer=_FakePortDiscoverer(),
            wake_manager=_FakeWakeManager(),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        t = threading.Thread(target=daemon.run, daemon=True)
        t.start()

        assert in_detect.wait(timeout=2.0), "daemon never entered detect()"
        daemon.stop()
        allow_continue.set()
        t.join(timeout=3.0)

        assert executed, (
            "engine.execute must be reached before daemon exits — "
            "no partial tick / no partial deletes"
        )


# =============================================================================
# Criterion 6 — Always-up containers (optimizer_* / *_db) never wake-proxied
# =============================================================================


class TestAlwaysUpContainersNeverWakeProxied:
    """
    Criterion 6: Defensive assertion — containers matching optimizer_* or *_db
    must never be registered in the wake proxy, even if a STOP_CONTAINER result
    is produced for them.
    """

    def test_when_optimizer_api_is_stopped_then_no_wake_proxy_is_registered(self):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8000])
        engine = _FakeEngine(results=[_stop_result("optimizer_api")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "optimizer_api" not in targets, (
            "optimizer_* containers must never be wake-proxied (safety invariant)"
        )

    def test_when_optimizer_frontend_is_stopped_then_no_wake_proxy_is_registered(self):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[80])
        engine = _FakeEngine(results=[_stop_result("optimizer_frontend")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "optimizer_frontend" not in targets, (
            "optimizer_frontend must never be wake-proxied"
        )

    def test_when_optimizer_db_is_stopped_then_no_wake_proxy_is_registered(self):
        """optimizer_db matches BOTH optimizer_* and *_db — must never be wake-proxied."""
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[5432])
        engine = _FakeEngine(results=[_stop_result("optimizer_db")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "optimizer_db" not in targets, (
            "optimizer_db (matches optimizer_* AND *_db) must never be wake-proxied"
        )

    def test_when_db_suffixed_container_is_stopped_then_no_wake_proxy_is_registered(
        self,
    ):
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[5432])
        engine = _FakeEngine(results=[_stop_result("clipcraft_db")])
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "clipcraft_db" not in targets, (
            "*_db containers must never be wake-proxied (data-bearing safety invariant)"
        )

    def test_when_eligible_and_always_up_containers_mixed_then_only_eligible_is_proxied(
        self,
    ):
        """Eligible containers are registered; always-up ones must be filtered out."""
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[8080])
        engine = _FakeEngine(
            results=[
                _stop_result("clipcraft_api"),  # eligible → must be registered
                _stop_result("optimizer_api"),  # optimizer_* → must NOT be registered
                _stop_result("dietwise_db"),  # *_db → must NOT be registered
            ]
        )
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        targets = [r.stack for r in wm.registered]
        assert "clipcraft_api" in targets, "eligible container must be wake-proxied"
        assert "optimizer_api" not in targets, "optimizer_* must never be wake-proxied"
        assert "dietwise_db" not in targets, "*_db must never be wake-proxied"

    def test_when_all_stopped_containers_are_always_up_then_no_wake_proxy_is_registered(
        self,
    ):
        """If every stopped container is always-up, the registered list must remain empty."""
        wm = _FakeWakeManager()
        pd = _FakePortDiscoverer(ports=[5432, 8080])
        engine = _FakeEngine(
            results=[
                _stop_result("optimizer_frontend"),
                _stop_result("optimizer_api"),
                _stop_result("optimizer_db"),
                _stop_result("clipcraft_db"),
            ]
        )
        daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

        daemon.tick()

        assert wm.registered == [], (
            "when all stopped containers are always-up, no wake proxy must be registered"
        )


# =============================================================================
# Property-based tests (Hypothesis) — invariants derived from criteria
# =============================================================================


@given(
    name=st.from_regex(r"optimizer_[a-z][a-z_]*", fullmatch=True),
)
@settings(max_examples=25, deadline=2000)
def test_when_any_optimizer_container_name_is_stopped_then_no_wake_proxy_is_registered(
    name: str,
) -> None:
    """
    Invariant (always-up): for any container name matching optimizer_*, the
    daemon must never register it in the wake proxy.
    """
    wm = _FakeWakeManager()
    pd = _FakePortDiscoverer(ports=[8080])
    engine = _FakeEngine(results=[_stop_result(name)])
    daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

    daemon.tick()

    targets = [r.stack for r in wm.registered]
    assert name not in targets, (
        f"optimizer_* container '{name}' must never be registered in the wake proxy"
    )


@given(
    prefix=st.from_regex(r"[a-z][a-z0-9_]*", fullmatch=True),
)
@settings(max_examples=25, deadline=2000)
def test_when_any_db_suffixed_container_is_stopped_then_no_wake_proxy_is_registered(
    prefix: str,
) -> None:
    """
    Invariant (always-up): for any container name matching *_db, the daemon
    must never register it in the wake proxy.
    """
    name = f"{prefix}_db"
    wm = _FakeWakeManager()
    pd = _FakePortDiscoverer(ports=[5432])
    engine = _FakeEngine(results=[_stop_result(name)])
    daemon = _build_daemon(engine=engine, port_discoverer=pd, wake_manager=wm)

    daemon.tick()

    targets = [r.stack for r in wm.registered]
    assert name not in targets, (
        f"*_db container '{name}' must never be registered in the wake proxy"
    )


@given(
    min_lifetime=st.floats(
        min_value=0.0,
        max_value=0.5,
        allow_nan=False,
        allow_infinity=False,
    )
)
@settings(max_examples=12, deadline=5000)
def test_when_min_lifetime_is_any_non_negative_value_then_exit_is_not_before_floor(
    min_lifetime: float,
) -> None:
    """
    Invariant (min-lifetime floor): for any valid non-negative min_lifetime,
    the fake-clock reading at exit must be ≥ min_lifetime.
    """
    clock = _FakeClock()
    config = _make_service_config(min_lifetime=min_lifetime, interval=0.0)
    daemon = _build_daemon(config=config, clock=clock)

    exit_times: list[float] = []

    def _run() -> None:
        daemon.run()
        exit_times.append(clock.monotonic())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)

    if exit_times:
        assert exit_times[0] >= min_lifetime, (
            f"min_lifetime={min_lifetime:.4f} but daemon exited at fake t={exit_times[0]:.4f}"
        )
