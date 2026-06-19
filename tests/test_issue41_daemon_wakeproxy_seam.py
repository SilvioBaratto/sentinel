"""Integration tests for issue #41: daemon↔wake-proxy wiring seam.

Exercises the real SentinelDaemon (via build_daemon) wired to the real
AsyncioWakeProxyManager (via build_wake_proxy) — no fake on the register()
seam.  A STOP_CONTAINER action flows all the way through to a bound TCP
listener.

Fake seams that ARE acceptable here:
  - FakePortDiscoverer: returns a StackPorts pointing at a loopback echo port
  - FakeRestarter: counts restart() calls, returns RESTARTED
  - AlwaysHealthyGate: immediately signals the upstream is up
  - FakePipeline / FakeDetect / FakeAdvisor / FakeEngine: standard collaborators

No fake on the WakeProxyManager.register() call path — the daemon calls the
real AsyncioWakeProxyManager.register(WakeRegistration) via the _make_registration
helper.
"""

from __future__ import annotations

import socket
import threading
import time

from sentinel.config import ServiceConfig, WakeProxyConfig
from sentinel.docker.wake_proxy import WakeComponents, build_wake_proxy
from sentinel.domain.value_objects import (
    PublishedPort,
    StackPorts,
    WakeOutcome,
    WakeRegistration,
)
from sentinel.service.daemon import build_daemon


# ─────────────────────────────────────────────────────────────────────────────
# Shared TCP helpers
# ─────────────────────────────────────────────────────────────────────────────


def _free_port() -> int:
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
    """Minimal threaded TCP echo server used as fake upstream container."""

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
# Fake wake-proxy adapters (injected into build_wake_proxy)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRestarter:
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
    async def wait_ready(self, port: int, timeout: float) -> bool:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Fake daemon collaborators
# ─────────────────────────────────────────────────────────────────────────────


class _FakePipeline:
    def step(self) -> object:
        return {"pressure": 2}


class _FakeDetect:
    def __call__(self, state: object) -> object:
        return {"candidates": []}


class _FakeAdvisor:
    def rank(self, detection: object) -> list:
        return []


class _FakeResult:
    def __init__(self, target: str) -> None:
        self.kind = "STOP_CONTAINER"
        self.target = target
        self.success = True


class _FakeEngine:
    def __init__(self, results: list) -> None:
        self._results = results

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


class _FakePortDiscoverer:
    """Returns a StackPorts whose ports tuple points at the given host port(s)."""

    def __init__(self, host_port: int, container_port: int) -> None:
        self._host_port = host_port
        self._container_port = container_port

    def discover(self, target: str) -> StackPorts:
        return StackPorts(
            stack=target,
            containers=(target,),
            ports=(
                PublishedPort(
                    host_ip="127.0.0.1",
                    host_port=self._host_port,
                    container_port=self._container_port,
                ),
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _make_service_config(**kw: object) -> ServiceConfig:
    defaults = {"interval": 0.0, "min_lifetime": 0.0, "exit_timeout": 5.0}
    defaults.update(kw)  # type: ignore[arg-type]
    return ServiceConfig(**defaults)  # type: ignore[arg-type]


def _make_wake_manager(
    restarter: _FakeRestarter,
    health_poll_interval: float = 0.05,
) -> object:
    config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=health_poll_interval)
    components = WakeComponents(restarter=restarter, health_gate=_AlwaysHealthyGate())
    return build_wake_proxy(config, components=components)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_when_daemon_tick_produces_stop_container_then_real_manager_binds_host_port():
    """
    AC: "A real STOP_CONTAINER registers a wake proxy on the real manager and
    binds the discovered published port(s)."

    The register() seam is NOT faked — daemon calls real AsyncioWakeProxyManager
    which actually binds the port.
    """
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    restarter = _FakeRestarter()
    clock = _FakeClock()

    wake_mgr = _make_wake_manager(restarter)
    port_disc = _FakePortDiscoverer(host_port=host_port, container_port=echo.port)
    engine = _FakeEngine(results=[_FakeResult("clipcraft_api")])

    daemon = build_daemon(
        _make_service_config(),
        pipeline=_FakePipeline(),
        detect=_FakeDetect(),
        advisor=_FakeAdvisor(),
        engine=engine,
        port_discoverer=port_disc,
        wake_manager=wake_mgr,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    try:
        daemon.tick()

        assert _is_bound(host_port), (
            f"real AsyncioWakeProxyManager must bind host_port={host_port} "
            "after a STOP_CONTAINER tick — no fake on the register() seam"
        )
        assert "clipcraft_api" in wake_mgr.active(), (
            "real manager must report clipcraft_api as active"
        )
    finally:
        wake_mgr.stop_all()
        echo.stop()


def test_when_client_connects_to_daemon_registered_port_then_restarter_is_invoked_once():
    """
    AC: "A real STOP_CONTAINER … binds the discovered published port(s)";
    client connecting triggers exactly one restart.

    Full seam: daemon.tick() → _make_registration → real register(WakeRegistration)
    → real AsyncioWakeProxyManager → WakePortListener → FakeRestarter.restart().
    """
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    restarter = _FakeRestarter()
    clock = _FakeClock()

    wake_mgr = _make_wake_manager(restarter)
    port_disc = _FakePortDiscoverer(host_port=host_port, container_port=echo.port)
    engine = _FakeEngine(results=[_FakeResult("dietwise_api")])

    daemon = build_daemon(
        _make_service_config(),
        pipeline=_FakePipeline(),
        detect=_FakeDetect(),
        advisor=_FakeAdvisor(),
        engine=engine,
        port_discoverer=port_disc,
        wake_manager=wake_mgr,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    try:
        daemon.tick()

        assert _is_bound(host_port), "port must be bound before client connects"

        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as conn:
            conn.settimeout(3.0)
            conn.sendall(b"sentinel-seam-test")
            conn.recv(64)
        time.sleep(0.15)

        assert restarter.restart_count == 1, (
            f"restarter must be invoked exactly once via the real register() seam; "
            f"got {restarter.restart_count}"
        )
    finally:
        wake_mgr.stop_all()
        echo.stop()


def test_when_daemon_tick_stops_container_then_register_receives_wake_registration_with_correct_stack():
    """
    AC: "register has a single agreed contract across protocol, impl, and caller."

    Verifies that the daemon builds a valid WakeRegistration (correct .stack,
    non-empty .ports, non-empty .restart_command) and passes it to register().
    Intercepted by a thin spy wrapper around the real manager.
    """
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    restarter = _FakeRestarter()
    clock = _FakeClock()

    real_mgr = _make_wake_manager(restarter)
    received: list[WakeRegistration] = []

    class _SpyManager:
        """Thin spy: records the WakeRegistration, then delegates to the real manager."""

        def register(self, registration: WakeRegistration) -> None:
            received.append(registration)
            real_mgr.register(registration)

        def stop_all(self) -> None:
            real_mgr.stop_all()

    port_disc = _FakePortDiscoverer(host_port=host_port, container_port=echo.port)
    engine = _FakeEngine(results=[_FakeResult("clipcraft_api")])

    daemon = build_daemon(
        _make_service_config(),
        pipeline=_FakePipeline(),
        detect=_FakeDetect(),
        advisor=_FakeAdvisor(),
        engine=engine,
        port_discoverer=port_disc,
        wake_manager=_SpyManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    try:
        daemon.tick()

        assert len(received) == 1, "exactly one WakeRegistration must be passed to register()"
        reg = received[0]
        assert isinstance(reg, WakeRegistration), (
            f"register() must receive a WakeRegistration; got {type(reg).__name__}"
        )
        assert reg.stack == "clipcraft_api", (
            f"WakeRegistration.stack must be the container name; got {reg.stack!r}"
        )
        assert len(reg.ports) >= 1, "WakeRegistration must include at least one PublishedPort"
        assert reg.ports[0].host_port == host_port
        assert len(reg.restart_command) >= 1, (
            "WakeRegistration.restart_command must be non-empty"
        )
    finally:
        real_mgr.stop_all()
        echo.stop()


class _OneshotEngine:
    """Returns results exactly once, then empty — prevents re-registering on every tick."""

    def __init__(self, results: list) -> None:
        self._results = results
        self._fired = False

    def execute(self, candidates: object, state: object) -> list:
        if self._fired:
            return []
        self._fired = True
        return self._results


def test_when_daemon_ticks_and_stop_is_called_then_real_manager_stop_all_frees_port():
    """
    AC: "sentinel run builds and starts the daemon without raising."
    (Shutdown path: wake proxy is released, port becomes free.)
    """
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    restarter = _FakeRestarter()
    clock = _FakeClock()

    wake_mgr = _make_wake_manager(restarter)
    port_disc = _FakePortDiscoverer(host_port=host_port, container_port=echo.port)
    engine = _OneshotEngine(results=[_FakeResult("clipcraft_api")])

    daemon = build_daemon(
        _make_service_config(interval=0.0, min_lifetime=0.0),
        pipeline=_FakePipeline(),
        detect=_FakeDetect(),
        advisor=_FakeAdvisor(),
        engine=engine,
        port_discoverer=port_disc,
        wake_manager=wake_mgr,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.1)
    daemon.stop()
    t.join(timeout=3.0)
    time.sleep(0.15)  # let asyncio tear-down propagate

    try:
        assert not t.is_alive(), "daemon thread must exit after stop()"
        assert not _is_bound(host_port), (
            f"port {host_port} must be freed after daemon shuts down (stop_all called)"
        )
    finally:
        echo.stop()
