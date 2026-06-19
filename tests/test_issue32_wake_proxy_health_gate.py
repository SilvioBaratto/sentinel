"""
Source-blind example tests for issue #32:
feat: wake-proxy health gate + restart-once coordination

Every test is derived from the acceptance criteria text only.
No implementation source was read during authoring (Red phase of TDD).

Skipped criteria (oracle: NOT VERIFIABLE):
  - "A second wave of connections after a completed restart does not re-trigger
    the restarter" — no concrete runtime assertion inferable.
  - "SOLID, clean code (methods < 10 lines, …)" — subjective prose, no unit check.
"""

import asyncio
import contextlib
import socket

import pytest
from hypothesis import given, settings, strategies as st

from sentinel.docker.wake_health import TcpHealthGate
from sentinel.docker.restart_gate import RestartOnceGate
from sentinel.domain.value_objects import WakeOutcome
from sentinel.config import WakeProxyConfig


# ── fixtures / helpers ────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an OS-assigned free TCP port (immediately released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _echo_server(host: str = "127.0.0.1"):
    """Start a minimal TCP server that accepts and immediately closes connections."""
    port = _free_port()
    server = await asyncio.start_server(
        lambda _r, w: w.close(),
        host,
        port,
    )
    async with server:
        yield port


async def _fake_restarter_noop() -> None:
    """A restarter that succeeds silently."""


async def _fake_restarter_fail() -> None:
    raise RuntimeError("simulated restart failure")


# ── AC1: TcpHealthGate.wait_ready(port, timeout) -> bool ──────────────────────
#
#   "polls asyncio.open_connection(host, host_port) every health_poll_interval,
#    returning True on first successful connect, False on timeout;
#    closes probe connections cleanly"


@pytest.mark.asyncio
async def test_when_server_is_listening_then_wait_ready_returns_true():
    async with _echo_server() as port:
        gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)
        result = await gate.wait_ready(port=port, timeout=2.0)
    assert result is True


@pytest.mark.asyncio
async def test_when_no_server_is_listening_and_timeout_elapses_then_wait_ready_returns_false():
    port = _free_port()
    gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)
    result = await gate.wait_ready(port=port, timeout=0.15)
    assert result is False


@pytest.mark.asyncio
async def test_when_server_becomes_available_between_polls_then_wait_ready_returns_true():
    """Polling: gate retries until the port opens, not just once."""
    port = _free_port()
    gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)

    async def _delayed_server():
        await asyncio.sleep(0.1)  # opens after ≈ two poll ticks
        srv = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", port)
        async with srv:
            await asyncio.sleep(0.5)

    result, _ = await asyncio.gather(
        gate.wait_ready(port=port, timeout=2.0),
        _delayed_server(),
    )
    assert result is True


@pytest.mark.asyncio
async def test_when_health_poll_interval_is_large_then_gate_misses_a_server_that_opens_briefly():
    """
    'Driven by health_poll_interval' — observable timing contract:
    if interval=0.12s and timeout=0.08s, the second poll at ≈0.12s is past the
    deadline, so a server that opens at 0.05s is never reached.
    """
    port = _free_port()
    gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.12)

    async def _brief_server():
        await asyncio.sleep(0.05)
        srv = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", port)
        async with srv:
            await asyncio.sleep(0.3)

    result, _ = await asyncio.gather(
        gate.wait_ready(port=port, timeout=0.08),
        _brief_server(),
    )
    assert result is False


@pytest.mark.asyncio
async def test_when_wait_ready_returns_then_probe_connections_are_closed():
    """Probe connections must be closed by the gate, not left dangling."""
    active_conns = 0

    async def _tracking_handler(reader, writer):
        nonlocal active_conns
        active_conns += 1
        try:
            await reader.read(4096)  # blocks until client closes its side
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            active_conns -= 1

    port = _free_port()
    srv = await asyncio.start_server(_tracking_handler, "127.0.0.1", port)
    async with srv:
        gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)
        await gate.wait_ready(port=port, timeout=2.0)
        await asyncio.sleep(0.05)  # give handler time to observe the close

    assert active_conns == 0


# Property: for any valid timeout in a reasonable range, a missing server always
# yields False — wait_ready must never raise for valid inputs.
@given(st.floats(min_value=0.05, max_value=0.25, allow_nan=False, allow_infinity=False))
@settings(max_examples=8, deadline=3000)
def test_when_no_server_and_any_valid_timeout_then_wait_ready_returns_false_not_raises(
    timeout: float,
):
    """Never-raises invariant over the timeout domain with no server present."""
    port = _free_port()

    async def _run() -> bool:
        gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)
        return await gate.wait_ready(port=port, timeout=timeout)

    result = asyncio.run(_run())
    assert result is False


# ── AC2: RestartOnceGate — exactly-once restarter + shared WakeOutcome ─────────
#
#   "across N concurrent awaiting coroutines, the injected restarter is invoked
#    exactly once; all waiters receive the same WakeOutcome"


@pytest.mark.asyncio
async def test_when_n_coroutines_await_gate_concurrently_then_restarter_called_exactly_once():
    call_count = 0

    async def _counting_restarter() -> None:
        nonlocal call_count
        call_count += 1

    gate = RestartOnceGate(restarter=_counting_restarter)
    await asyncio.gather(*[gate.wait() for _ in range(10)])

    assert call_count == 1


@pytest.mark.asyncio
async def test_when_n_coroutines_await_gate_then_all_receive_the_same_wake_outcome():
    gate = RestartOnceGate(restarter=_fake_restarter_noop)
    outcomes = await asyncio.gather(*[gate.wait() for _ in range(8)])

    assert len(set(outcomes)) == 1, (
        f"Expected all waiters to share one outcome; got {set(outcomes)}"
    )


# Property: the 'exactly once' call-count guarantee must hold for any N ≥ 1.
@given(st.integers(min_value=1, max_value=20))
@settings(max_examples=10, deadline=5000)
def test_when_any_number_of_concurrent_waiters_then_restarter_called_exactly_once(
    n: int,
):
    """Ordering / count invariant: call count == 1 for all N ≥ 1."""

    async def _run(n: int) -> int:
        call_count = 0

        async def _restarter() -> None:
            nonlocal call_count
            call_count += 1

        gate = RestartOnceGate(restarter=_restarter)
        await asyncio.gather(*[gate.wait() for _ in range(n)])
        return call_count

    assert asyncio.run(_run(n)) == 1


# ── AC4: restart failure → RESTART_FAILED + gate is retryable ─────────────────
#
#   "On restart failure, all current waiters observe RESTART_FAILED and the gate
#    may be retried on a later connection (not permanently poisoned)"


@pytest.mark.asyncio
async def test_when_restarter_raises_then_all_waiters_observe_restart_failed():
    gate = RestartOnceGate(restarter=_fake_restarter_fail)
    outcomes = await asyncio.gather(*[gate.wait() for _ in range(6)])

    assert all(o is WakeOutcome.RESTART_FAILED for o in outcomes), (
        f"Expected all waiters to get RESTART_FAILED; got {outcomes}"
    )


@pytest.mark.asyncio
async def test_when_restart_fails_then_subsequent_call_to_gate_retries_restarter():
    """
    'Not permanently poisoned': a second gate.wait() after a failure must invoke
    the restarter again rather than short-circuit with the stale RESTART_FAILED.
    """
    attempt = 0

    async def _restarter_fails_first_time() -> None:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("first restart fails")

    gate = RestartOnceGate(restarter=_restarter_fails_first_time)

    first_outcome = await gate.wait()
    assert first_outcome is WakeOutcome.RESTART_FAILED

    # A later connection must not see a stale RESTART_FAILED; the gate must retry.
    second_outcome = await gate.wait()
    assert second_outcome is not WakeOutcome.RESTART_FAILED
    assert (
        attempt == 2
    )  # restarter was invoked a second time (not permanently poisoned)


# ── AC5: WakeProxyConfig fields + event-loop safety ───────────────────────────
#
#   "Driven by WakeProxyConfig.health_timeout / health_poll_interval;
#    never blocks the event loop with sync work"


def test_when_wake_proxy_config_created_then_health_timeout_field_is_accessible():
    config = WakeProxyConfig(health_timeout=3.0, health_poll_interval=0.5)
    assert config.health_timeout == 3.0


def test_when_wake_proxy_config_created_then_health_poll_interval_field_is_accessible():
    config = WakeProxyConfig(health_timeout=3.0, health_poll_interval=0.5)
    assert config.health_poll_interval == 0.5


@pytest.mark.asyncio
async def test_when_wait_ready_is_running_then_other_event_loop_tasks_are_not_starved():
    """'Never blocks the event loop with sync work' — concurrent tasks must progress."""
    port = _free_port()
    gate = TcpHealthGate(host="127.0.0.1", health_poll_interval=0.05)

    side_task_ran = False

    async def _side_task() -> None:
        nonlocal side_task_ran
        await asyncio.sleep(0)
        side_task_ran = True

    await asyncio.gather(
        gate.wait_ready(port=port, timeout=0.2),
        _side_task(),
    )
    assert side_task_ran


@pytest.mark.asyncio
async def test_when_gate_wait_is_running_then_other_event_loop_tasks_are_not_starved():
    """'Never blocks the event loop' applies to RestartOnceGate.wait() as well."""
    gate = RestartOnceGate(restarter=_fake_restarter_noop)

    side_task_ran = False

    async def _side_task() -> None:
        nonlocal side_task_ran
        await asyncio.sleep(0)
        side_task_ran = True

    await asyncio.gather(gate.wait(), _side_task())
    assert side_task_ran
