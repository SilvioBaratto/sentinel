"""Sentinel daemon run-loop — Cycle 4.

SentinelDaemon wires pipeline / detection / engine / advisor into a timed
loop with clean SIGTERM shutdown, sliced inter-tick sleep, and a
minimum-lifetime floor.  All OS-level imports (signal) are deferred to
run() so the module is safe to import in test contexts.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from sentinel.config import ServiceConfig
from sentinel.domain.protocols import Advisor, PortDiscoverer, WakeProxyManager

# Maximum time blocked inside one sleep slice before rechecking stop event.
_SLICE: float = 0.25


# ── helpers (pure, no IO) ─────────────────────────────────────────────────────


def _is_stop_container(result: object) -> bool:
    """True for both real ActionKind.STOP_CONTAINER and the string "STOP_CONTAINER"."""
    k = getattr(result, "kind", None)
    val = getattr(k, "value", str(k))
    return val.lower() == "stop_container"


def _make_registration(stack_ports: Any) -> Any:
    """Build a WakeRegistration from the StackPorts returned by port_discoverer."""
    from sentinel.domain.value_objects import WakeRegistration  # noqa: PLC0415

    project = getattr(stack_ports, "compose_project", None) or stack_ports.stack
    return WakeRegistration(
        stack=stack_ports.stack,
        ports=stack_ports.ports,
        restart_command=("compose", "-p", project, "up", "-d"),
    )


def _reorder(detection: Any, ranking: Any) -> Any:
    """Reshuffle detection.containers by advisor ordering; fall back on any error."""
    order: tuple[Any, ...] = tuple(getattr(ranking, "ordered_targets", None) or ())
    if not order:
        return detection
    containers: tuple[Any, ...] = tuple(getattr(detection, "containers", None) or ())
    if not containers:
        return detection
    by_name = {c.name: c for c in containers}
    front = [by_name[n] for n in order if n in by_name]
    ranked = frozenset(order)
    rest = [c for c in containers if c.name not in ranked]
    try:
        from dataclasses import replace  # noqa: PLC0415

        return replace(detection, containers=tuple(front + rest))
    except Exception:
        return detection


def _state_to_str(state: Any) -> str:
    """Extract a JSON-safe string from an opaque pipeline state object."""
    if state is None:
        return "normal"
    if hasattr(state, "value"):
        return str(state.value)
    if isinstance(state, str):
        return state
    return "normal"


# ── SentinelDaemon ───────────────────────────────────────────────────────────


class SentinelDaemon:
    """Drives one tick per interval; shuts down cleanly on stop() / SIGTERM."""

    def __init__(
        self,
        pipeline: Any,
        detect: Callable,
        advisor: Advisor,
        engine: Any,
        port_discoverer: PortDiscoverer,
        wake_manager: WakeProxyManager,
        config: ServiceConfig,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        allow_list: Any = None,
        state_path: Path | str | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._detect = detect
        self._advisor = advisor
        self._engine = engine
        self._port_disc = port_discoverer
        self._wake = wake_manager
        self._config = config
        self._monotonic = monotonic
        self._sleep = sleep
        self._allow_list = allow_list
        self._state_path = Path(state_path) if state_path is not None else None
        self._stop = threading.Event()
        self.snapshot: Any = None

    # ── public API ────────────────────────────────────────────────────────────

    def tick(self) -> None:
        state = self._pipeline.step()  # type: ignore[attr-defined]
        detection = self._detect(state)
        ranking = self._advisor.rank(detection)
        reordered = _reorder(detection, ranking)
        results = self._engine.execute(reordered, state)  # type: ignore[attr-defined]
        self._post_tick(state, results)

    def run(self) -> None:
        import signal  # deferred: no module-level OS import  # noqa: PLC0415
        import threading  # noqa: PLC0415

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._on_signal)
            signal.signal(signal.SIGINT, self._on_signal)
        start = self._monotonic()
        while not self._stop.is_set():
            self.tick()
            self._sliced_sleep(self._config.interval)
        self._shutdown(start)

    def stop(self) -> None:
        self._stop.set()

    # ── internals ─────────────────────────────────────────────────────────────

    def _on_signal(self, _signum: int, _frame: Any) -> None:
        self._stop.set()

    def _post_tick(self, state: Any, results: Any) -> None:
        self.snapshot = state
        for r in results or ():
            if (
                _is_stop_container(r)
                and r.success
                and self._is_proxy_eligible(r.target)
            ):
                stack_ports = self._port_disc.discover(r.target)
                self._wake.register(_make_registration(stack_ports))
        self._flush_snapshot()

    def _is_proxy_eligible(self, name: str) -> bool:
        if self._allow_list is not None:
            return self._allow_list.is_eligible(name)  # type: ignore[attr-defined]
        return not (name.startswith("optimizer_") or name.endswith("_db"))

    def _sliced_sleep(self, total: float) -> None:
        remaining = total
        while remaining > 0 and not self._stop.is_set():
            self._sleep(min(_SLICE, remaining))
            remaining -= _SLICE

    def _shutdown(self, start: float) -> None:
        self._wake.stop_all()
        self._flush_snapshot()
        elapsed = self._monotonic() - start
        if elapsed < self._config.min_lifetime:
            self._sleep(self._config.min_lifetime - elapsed)

    def _flush_snapshot(self) -> None:
        """Atomically write the state snapshot to disk; silently drops on any error."""
        if self._state_path is None:
            return
        try:
            wake_proxies = list(getattr(self._wake, "active", lambda: ())())
            data = {
                "state": _state_to_str(self.snapshot),
                "wake_proxies": wake_proxies,
            }
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.parent / (self._state_path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception:
            pass


# ── composition root ──────────────────────────────────────────────────────────


def build_daemon(
    config: ServiceConfig,
    *,
    pipeline: Any,
    detect: Callable,
    advisor: Advisor,
    engine: Any,
    port_discoverer: PortDiscoverer,
    wake_manager: WakeProxyManager,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    allow_list: Any = None,
    state_path: Path | str | None = None,
) -> SentinelDaemon:
    """Wire all collaborators into a SentinelDaemon; no OS imports at call time."""
    if allow_list is None:
        from sentinel.docker.allow_list import ContainerAllowList  # noqa: PLC0415

        allow_list = ContainerAllowList()
    return SentinelDaemon(
        pipeline=pipeline,
        detect=detect,
        advisor=advisor,
        engine=engine,
        port_discoverer=port_discoverer,
        wake_manager=wake_manager,
        config=config,
        monotonic=monotonic,
        sleep=sleep,
        allow_list=allow_list,
        state_path=state_path,
    )
