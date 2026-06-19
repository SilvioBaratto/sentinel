"""Source-blind example tests for issue #33:
feat: wake-proxy asyncio listener + manager + composition root

Every test is derived from the acceptance criteria text only (Red phase TDD).
No implementation source was read during authoring.

Design choices (from criteria text, not from source):
  - WakeProxyManager exposes synchronous register(), unregister(), stop_all(), active()
  - An internal asyncio event loop (daemon thread) drives the per-port listeners
  - build_wake_proxy(config, *, components=None) is the public composition root
  - WakeComponents bundles the injected adapters (restarter + health_gate)
  - Proxy forwards bytes to upstream after restart + health gate;
    'upstream' is reached via container_port (simplest reading consistent with criteria)
  - After register() returns, the listener(s) are bound and ready to accept connections

Skipped criteria (oracle: NOT VERIFIABLE):
  - Multi-port concurrent first-hits cause a single restart — no concrete runtime assertion
  - SOLID, clean code (subjective prose, no unit assertion)
"""

from __future__ import annotations

import inspect
import socket
import threading
import time

from hypothesis import given, settings, strategies as st

from sentinel.domain.value_objects import PublishedPort, WakeRegistration, WakeOutcome
from sentinel.config import WakeProxyConfig
from sentinel.docker.wake_manager import WakeProxyManager
from sentinel.docker.wake_proxy import build_wake_proxy, WakeComponents


# ── helpers ───────────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an OS-assigned free TCP port (socket released immediately)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_is_bound(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if something is accepting connections on host:port."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError):
        return False


class _TcpEchoServer:
    """Threaded TCP echo server used as a fake upstream container."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        self.host = host
        self.port: int = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "_TcpEchoServer":
        self._sock.listen(10)
        self._sock.settimeout(0.1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
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

    def _serve(self) -> None:
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


class _FakeRestarter:
    """Fake StackRestarter: records restart() calls, always returns RESTARTED."""

    def __init__(self) -> None:
        self.restart_count = 0
        self._lock = threading.Lock()

    def restart(self, reg: WakeRegistration) -> WakeOutcome:
        with self._lock:
            self.restart_count += 1
        return WakeOutcome.RESTARTED

    def is_running(self, name: str) -> bool:
        return False  # always stopped so restart is triggered


class _FailingRestarter:
    """Fake StackRestarter that always signals RESTART_FAILED."""

    def restart(self, reg: WakeRegistration) -> WakeOutcome:
        return WakeOutcome.RESTART_FAILED

    def is_running(self, name: str) -> bool:
        return False


class _AlwaysHealthyGate:
    """Fake health gate: immediately reports the upstream as healthy."""

    async def wait_ready(self, port: int, timeout: float) -> bool:
        return True


class _AlwaysTimingOutGate:
    """Fake health gate: always times out, regardless of upstream state."""

    async def wait_ready(self, port: int, timeout: float) -> bool:
        return False


def _make_registration(
    stack: str,
    host_port: int,
    container_port: int,
    host_ip: str = "127.0.0.1",
) -> WakeRegistration:
    return WakeRegistration(
        stack=stack,
        ports=(
            PublishedPort(
                host_ip=host_ip, host_port=host_port, container_port=container_port
            ),
        ),
        restart_command=("compose", "-p", stack, "up", "-d"),
    )


def _make_manager(
    restarter: object | None = None,
    health_gate: object | None = None,
    health_timeout: float = 2.0,
    health_poll_interval: float = 0.05,
) -> WakeProxyManager:
    """Build a WakeProxyManager wired with fake adapters for unit tests."""
    config = WakeProxyConfig(
        health_timeout=health_timeout,
        health_poll_interval=health_poll_interval,
    )
    components = WakeComponents(
        restarter=restarter or _FakeRestarter(),
        health_gate=health_gate or _AlwaysHealthyGate(),
    )
    return build_wake_proxy(config, components=components)


# ===========================================================================
# AC1 — register(WakeRegistration) binds one listener per PublishedPort,
#         all sharing one RestartOnceGate for the stack
# ===========================================================================


def test_when_register_is_called_then_listener_is_bound_on_published_host_port():
    host_port = _free_port()
    manager = _make_manager()

    manager.register(
        _make_registration("s", host_port=host_port, container_port=_free_port())
    )

    try:
        assert _port_is_bound(host_port), (
            f"Expected listener on port {host_port} immediately after register()"
        )
    finally:
        manager.stop_all()


def test_when_register_is_called_with_two_ports_then_both_host_ports_are_bound():
    port_a, port_b = _free_port(), _free_port()
    reg = WakeRegistration(
        stack="multiport",
        ports=(
            PublishedPort(host_ip="127.0.0.1", host_port=port_a, container_port=8001),
            PublishedPort(host_ip="127.0.0.1", host_port=port_b, container_port=8002),
        ),
        restart_command=("compose", "-p", "multiport", "up", "-d"),
    )
    manager = _make_manager()

    manager.register(reg)

    try:
        assert _port_is_bound(port_a), f"Expected listener on port_a={port_a}"
        assert _port_is_bound(port_b), f"Expected listener on port_b={port_b}"
    finally:
        manager.stop_all()


def test_when_two_different_stacks_are_registered_then_both_listen_independently():
    port_a, port_b = _free_port(), _free_port()
    manager = _make_manager()

    manager.register(
        _make_registration("alpha", host_port=port_a, container_port=_free_port())
    )
    manager.register(
        _make_registration("beta", host_port=port_b, container_port=_free_port())
    )

    try:
        assert _port_is_bound(port_a)
        assert _port_is_bound(port_b)
    finally:
        manager.stop_all()


# ===========================================================================
# AC2 — First inbound connection triggers exactly one restart, awaits the
#         health gate, then forwards bytes both directions until either side
#         closes
# ===========================================================================


def test_when_first_connection_arrives_then_restarter_is_called_exactly_once():
    echo = _TcpEchoServer().start()
    restarter = _FakeRestarter()
    host_port = _free_port()
    manager = _make_manager(restarter=restarter)
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=echo.port)
    )

    try:
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as conn:
            conn.settimeout(3.0)
            conn.sendall(b"hi")
            conn.recv(64)
        time.sleep(0.2)
        assert restarter.restart_count == 1, (
            f"Restart must be called exactly once on first connection; "
            f"got {restarter.restart_count}"
        )
    finally:
        manager.stop_all()
        echo.stop()


def test_when_connection_arrives_then_bytes_are_forwarded_to_upstream_and_echoed_back():
    """Bidirectional forwarding: data client→proxy→upstream→proxy→client (echo)."""
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=echo.port)
    )

    payload = b"sentinel-proxy-test"
    try:
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as conn:
            conn.settimeout(3.0)
            conn.sendall(payload)
            received = conn.recv(len(payload))
        assert received == payload, f"Expected echo {payload!r}; got {received!r}"
    finally:
        manager.stop_all()
        echo.stop()


def test_when_client_closes_connection_then_listener_remains_bound_for_next_client():
    """'Forwards until either side closes' — client-close must not kill the listener."""
    echo = _TcpEchoServer().start()
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=echo.port)
    )

    try:
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as conn:
            conn.settimeout(1.0)
            conn.sendall(b"bye")
            conn.recv(64)
        time.sleep(0.15)
        assert _port_is_bound(host_port), (
            "Listener must remain bound after client closes its end"
        )
    finally:
        manager.stop_all()
        echo.stop()


# ===========================================================================
# AC4 — unregister(stack) / stop_all() close listeners and free ports;
#         active()->tuple[str,...] reflects currently-bound stacks
# ===========================================================================


def test_when_no_stacks_are_registered_then_active_returns_empty_tuple():
    manager = _make_manager()
    try:
        assert manager.active() == ()
    finally:
        manager.stop_all()


def test_when_stack_is_registered_then_stack_name_appears_in_active():
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("mystack", host_port=host_port, container_port=_free_port())
    )

    try:
        assert "mystack" in manager.active()
    finally:
        manager.stop_all()


def test_when_stack_is_registered_then_active_returns_a_tuple():
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("mystack", host_port=host_port, container_port=_free_port())
    )

    try:
        result = manager.active()
        assert isinstance(result, tuple), (
            f"active() must return tuple; got {type(result).__name__}"
        )
    finally:
        manager.stop_all()


def test_when_stack_is_unregistered_then_its_name_is_absent_from_active():
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("mystack", host_port=host_port, container_port=_free_port())
    )

    manager.unregister("mystack")

    try:
        assert "mystack" not in manager.active()
    finally:
        manager.stop_all()


def test_when_unregister_is_called_then_the_port_is_no_longer_bound():
    host_port = _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("mystack", host_port=host_port, container_port=_free_port())
    )
    assert _port_is_bound(host_port), "Precondition: listener bound after register()"

    manager.unregister("mystack")
    time.sleep(0.15)

    try:
        assert not _port_is_bound(host_port), (
            f"Port {host_port} must be freed after unregister()"
        )
    finally:
        manager.stop_all()


def test_when_stop_all_is_called_then_all_registered_ports_are_freed():
    port_a, port_b = _free_port(), _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("alpha", host_port=port_a, container_port=_free_port())
    )
    manager.register(
        _make_registration("beta", host_port=port_b, container_port=_free_port())
    )

    manager.stop_all()
    time.sleep(0.15)

    assert not _port_is_bound(port_a), "port_a must be freed after stop_all()"
    assert not _port_is_bound(port_b), "port_b must be freed after stop_all()"


def test_when_stop_all_is_called_then_active_returns_empty_tuple():
    manager = _make_manager()
    manager.register(
        _make_registration("alpha", host_port=_free_port(), container_port=_free_port())
    )
    manager.register(
        _make_registration("beta", host_port=_free_port(), container_port=_free_port())
    )

    manager.stop_all()

    assert manager.active() == ()


def test_when_only_one_stack_unregistered_then_other_stack_remains_active():
    port_a, port_b = _free_port(), _free_port()
    manager = _make_manager()
    manager.register(
        _make_registration("alpha", host_port=port_a, container_port=_free_port())
    )
    manager.register(
        _make_registration("beta", host_port=port_b, container_port=_free_port())
    )

    manager.unregister("alpha")

    try:
        assert "alpha" not in manager.active()
        assert "beta" in manager.active()
        assert _port_is_bound(port_b), "beta's port must remain bound"
    finally:
        manager.stop_all()


# ===========================================================================
# AC5 — WakeProxyManager methods are synchronous and thread-safe;
#         the asyncio loop is fully confined to docker/wake_*
# ===========================================================================


def test_when_register_is_called_then_return_value_is_not_a_coroutine():
    manager = _make_manager()
    reg = _make_registration("s", host_port=_free_port(), container_port=_free_port())
    result = manager.register(reg)

    try:
        assert not inspect.iscoroutine(result), (
            "register() must be synchronous (not return a coroutine)"
        )
    finally:
        if inspect.iscoroutine(result):
            result.close()
        manager.stop_all()


def test_when_unregister_is_called_then_return_value_is_not_a_coroutine():
    manager = _make_manager()
    manager.register(
        _make_registration("s", host_port=_free_port(), container_port=_free_port())
    )
    result = manager.unregister("s")

    try:
        assert not inspect.iscoroutine(result), (
            "unregister() must be synchronous (not return a coroutine)"
        )
    finally:
        if inspect.iscoroutine(result):
            result.close()
        manager.stop_all()


def test_when_active_is_called_then_return_value_is_not_a_coroutine():
    manager = _make_manager()
    result = manager.active()

    try:
        assert not inspect.iscoroutine(result), (
            "active() must be synchronous (not return a coroutine)"
        )
    finally:
        if inspect.iscoroutine(result):
            result.close()
        manager.stop_all()


def test_when_stop_all_is_called_then_return_value_is_not_a_coroutine():
    manager = _make_manager()
    result = manager.stop_all()

    assert not inspect.iscoroutine(result), (
        "stop_all() must be synchronous (not return a coroutine)"
    )
    if inspect.iscoroutine(result):
        result.close()


def test_when_register_and_unregister_called_from_multiple_threads_then_no_exception_is_raised():
    """Thread-safety: concurrent register/unregister from N threads must not raise."""
    manager = _make_manager()
    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        try:
            reg = _make_registration(
                f"stack_{idx}",
                host_port=_free_port(),
                container_port=_free_port(),
            )
            manager.register(reg)
            manager.unregister(f"stack_{idx}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    try:
        assert not errors, f"Thread-safety violation; exceptions: {errors}"
    finally:
        manager.stop_all()


def test_when_active_is_called_concurrently_from_multiple_threads_then_no_exception_is_raised():
    """active() must be safe to call from any thread."""
    manager = _make_manager()
    errors: list[Exception] = []

    def _read() -> None:
        try:
            _ = manager.active()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)

    try:
        assert not errors, f"Thread-safety violation on active(): {errors}"
    finally:
        manager.stop_all()


# ===========================================================================
# AC6 — build_wake_proxy(config, *, components=None) wires real adapters or
#         injected fakes; importing the module spawns no subprocess and binds
#         no socket
# ===========================================================================


def test_when_build_wake_proxy_is_called_with_components_then_a_wake_proxy_manager_is_returned():
    config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=0.05)
    components = WakeComponents(
        restarter=_FakeRestarter(),
        health_gate=_AlwaysHealthyGate(),
    )

    manager = build_wake_proxy(config, components=components)

    try:
        assert isinstance(manager, WakeProxyManager)
    finally:
        manager.stop_all()


def test_when_build_wake_proxy_is_called_without_components_then_a_manager_is_returned():
    """components=None uses real adapters; construction must not raise."""
    config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=0.05)

    manager = build_wake_proxy(config)

    try:
        assert isinstance(manager, WakeProxyManager)
    finally:
        manager.stop_all()


def test_when_build_wake_proxy_is_called_then_no_port_is_bound_before_register():
    """Construction alone must not bind any socket; only register() binds ports."""
    config = WakeProxyConfig(health_timeout=2.0, health_poll_interval=0.05)
    components = WakeComponents(
        restarter=_FakeRestarter(),
        health_gate=_AlwaysHealthyGate(),
    )

    manager = build_wake_proxy(config, components=components)

    try:
        assert manager.active() == (), (
            "No stacks must be active immediately after build_wake_proxy(); "
            "ports are only bound via register()"
        )
    finally:
        manager.stop_all()


def test_when_wake_proxy_module_is_imported_then_no_new_listening_socket_is_bound():
    """
    'Importing the module spawns no subprocess and binds no socket.'
    The number of LISTEN sockets in this process must not increase on (re)import.
    """
    import importlib
    import psutil

    proc = psutil.Process()
    before = {c.laddr.port for c in proc.connections("inet") if c.status == "LISTEN"}

    import sentinel.docker.wake_proxy as _mod

    importlib.reload(_mod)

    after = {c.laddr.port for c in proc.connections("inet") if c.status == "LISTEN"}
    new_ports = after - before

    assert not new_ports, (
        f"Importing sentinel.docker.wake_proxy bound new sockets on ports: {new_ports}"
    )


# ===========================================================================
# AC7 — HEALTH_TIMEOUT / restart failure closes the client connection cleanly
#         and leaves the listener bound for retry (no hung sockets)
# ===========================================================================


def test_when_health_gate_times_out_then_client_connection_is_closed_by_proxy():
    """
    Proxy must close the inbound connection when the health gate times out,
    rather than holding it open indefinitely (no hung sockets).
    """
    host_port = _free_port()
    manager = _make_manager(
        health_gate=_AlwaysTimingOutGate(),
        health_timeout=0.1,
    )
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=_free_port())
    )

    try:
        conn = socket.create_connection(("127.0.0.1", host_port), timeout=3.0)
        conn.settimeout(3.0)
        try:
            data = conn.recv(1024)
            assert data == b"", (
                "Proxy must close connection on health timeout (recv → b'')"
            )
        except ConnectionResetError:
            pass  # RST is also an acceptable clean-close signal
        finally:
            conn.close()
    finally:
        manager.stop_all()


def test_when_health_gate_times_out_then_listener_remains_bound_for_retry():
    """After health timeout the listener must stay bound so the next client can retry."""
    host_port = _free_port()
    manager = _make_manager(
        health_gate=_AlwaysTimingOutGate(),
        health_timeout=0.1,
    )
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=_free_port())
    )

    try:
        try:
            with socket.create_connection(
                ("127.0.0.1", host_port), timeout=3.0
            ) as conn:
                conn.settimeout(3.0)
                conn.recv(1024)
        except (ConnectionResetError, OSError):
            pass

        time.sleep(0.2)
        assert _port_is_bound(host_port), (
            "Listener must remain bound after health timeout for retry"
        )
    finally:
        manager.stop_all()


def test_when_restart_fails_then_client_connection_is_closed_by_proxy():
    """Restart failure must close the inbound client connection cleanly."""
    host_port = _free_port()
    manager = _make_manager(restarter=_FailingRestarter())
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=_free_port())
    )

    try:
        conn = socket.create_connection(("127.0.0.1", host_port), timeout=3.0)
        conn.settimeout(3.0)
        try:
            data = conn.recv(1024)
            assert data == b"", (
                "Proxy must close connection on restart failure (recv → b'')"
            )
        except ConnectionResetError:
            pass
        finally:
            conn.close()
    finally:
        manager.stop_all()


def test_when_restart_fails_then_listener_remains_bound_for_retry():
    """After restart failure the listener must stay bound so the next client can retry."""
    host_port = _free_port()
    manager = _make_manager(restarter=_FailingRestarter())
    manager.register(
        _make_registration("svc", host_port=host_port, container_port=_free_port())
    )

    try:
        try:
            with socket.create_connection(
                ("127.0.0.1", host_port), timeout=3.0
            ) as conn:
                conn.settimeout(3.0)
                conn.recv(1024)
        except (ConnectionResetError, OSError):
            pass

        time.sleep(0.2)
        assert _port_is_bound(host_port), (
            "Listener must remain bound after restart failure for retry"
        )
    finally:
        manager.stop_all()


# ===========================================================================
# AC8 — All tests pass (fake restarter + loopback echo upstream;
#         assert restart-once + bidirectional forwarding + teardown)
# ===========================================================================


def test_integration_restart_once_bidirectional_forwarding_and_clean_teardown():
    """
    Full AC8 scenario:
      1. Register a stack with a loopback echo server as the upstream.
      2. Connect — assert restart triggered exactly once.
      3. Send bytes — assert same bytes echoed back (bidirectional forwarding).
      4. stop_all() — assert port is freed (clean teardown, no hung sockets).
    """
    echo = _TcpEchoServer().start()
    restarter = _FakeRestarter()
    host_port = _free_port()
    manager = _make_manager(
        restarter=restarter, health_timeout=2.0, health_poll_interval=0.05
    )
    manager.register(
        _make_registration("clipcraft", host_port=host_port, container_port=echo.port)
    )

    payload = b"wake-proxy-integration"
    try:
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as conn:
            conn.settimeout(3.0)
            conn.sendall(payload)
            received = conn.recv(len(payload))

        time.sleep(0.2)

        # restart-once
        assert restarter.restart_count == 1, (
            f"Expected exactly 1 restart; got {restarter.restart_count}"
        )
        # bidirectional forwarding
        assert received == payload, f"Expected echo {payload!r}; got {received!r}"

        # teardown
        manager.stop_all()
        time.sleep(0.1)
        assert not _port_is_bound(host_port), "Port must be free after stop_all()"
    finally:
        manager.stop_all()  # idempotent safety
        echo.stop()


def test_integration_second_connection_after_restart_does_not_restart_again():
    """
    'First inbound connection' — a second connection after the stack is up
    must not trigger another restart (the gate is shared and already completed).
    """
    echo = _TcpEchoServer().start()
    restarter = _FakeRestarter()
    host_port = _free_port()
    manager = _make_manager(
        restarter=restarter, health_timeout=2.0, health_poll_interval=0.05
    )
    manager.register(
        _make_registration("clipcraft", host_port=host_port, container_port=echo.port)
    )

    try:
        # first connection — triggers restart
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as c1:
            c1.settimeout(3.0)
            c1.sendall(b"first")
            c1.recv(64)
        time.sleep(0.2)

        first_count = restarter.restart_count
        assert first_count == 1, (
            f"First connection must restart exactly once; got {first_count}"
        )

        # second connection — stack already up; restart must NOT fire again
        with socket.create_connection(("127.0.0.1", host_port), timeout=3.0) as c2:
            c2.settimeout(3.0)
            c2.sendall(b"second")
            c2.recv(64)
        time.sleep(0.2)

        assert restarter.restart_count == first_count, (
            "Second connection must not trigger another restart"
        )
    finally:
        manager.stop_all()
        echo.stop()


# ===========================================================================
# Property-based tests (Hypothesis) — invariants derived from criteria
# ===========================================================================


@given(st.text(min_size=1, max_size=40))
@settings(max_examples=20, deadline=3000)
def test_when_unregister_is_called_for_any_unknown_stack_then_no_exception_is_raised(
    name: str,
) -> None:
    """
    Never-raises invariant: unregister() on a name that was never registered
    must be a no-op (criteria: 'close listeners and free ports' — nothing to do).
    """
    manager = _make_manager()
    try:
        manager.unregister(name)  # must not raise for any string
    finally:
        manager.stop_all()


@given(st.text(min_size=1, max_size=40))
@settings(max_examples=20, deadline=3000)
def test_when_active_is_called_in_any_state_then_result_is_always_a_tuple(
    _ignored: str,
) -> None:
    """
    Type invariant: active() always returns a tuple[str, ...] regardless of state.
    """
    manager = _make_manager()
    try:
        result = manager.active()
        assert isinstance(result, tuple), (
            f"active() must always return tuple; got {type(result).__name__}"
        )
        for item in result:
            assert isinstance(item, str), (
                f"active() elements must be str; got {type(item).__name__}"
            )
    finally:
        manager.stop_all()


@given(st.integers(min_value=1, max_value=4))
@settings(max_examples=10, deadline=10_000)
def test_when_n_stacks_registered_then_active_always_contains_each_stack_name(
    n: int,
) -> None:
    """
    Ordering invariant: for any N ≥ 1 registered stacks, active() must contain
    every registered stack name.
    """
    manager = _make_manager()
    stacks = [f"stack_{i}" for i in range(n)]
    try:
        for name in stacks:
            manager.register(
                _make_registration(
                    name, host_port=_free_port(), container_port=_free_port()
                )
            )
        active = manager.active()
        for name in stacks:
            assert name in active, (
                f"Registered stack '{name}' must appear in active(); got {active}"
            )
    finally:
        manager.stop_all()
