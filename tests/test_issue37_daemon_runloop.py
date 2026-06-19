"""
Source-blind tests for Issue #37: daemon run-loop + SIGTERM graceful shutdown
+ min-lifetime guard.

All tests are derived from acceptance-criteria text alone.  No src/ files were read.

Design assumptions recorded here (implementation does not exist yet):

  from sentinel.service.daemon import build_daemon
  from sentinel.config import ServiceConfig

  build_daemon(
      config: ServiceConfig,
      *,
      pipeline,        # has .step() -> state
      detect,          # callable(state) -> detection
      advisor,         # has .rank(detection) -> ranking / reorder-hint
      engine,          # has .execute(candidates, state) -> list[ActionResult]
      port_discoverer, # has .discover(target: str) -> list[int]
      wake_manager,    # has .register(target, ports) and .stop_all()
      monotonic,       # callable() -> float  (injectable fake clock)
      sleep,           # callable(seconds: float) -> None  (injectable fake sleep)
  ) -> Daemon

  Daemon interface:
    .tick()     — run exactly one tick (pipeline → detect → rank → reorder → execute)
    .run()      — start the event loop; registers SIGTERM handler
    .stop()     — signal the internal stop event (unit-test equivalent of SIGTERM)
    .snapshot   — last pipeline state written on shutdown; None before any shutdown

  ActionResult attributes inferred from criteria:
    .kind: str       — e.g. "STOP_CONTAINER"
    .success: bool
    .target: str     — container name

  ServiceConfig fields used:
    .interval: float       — inter-tick sleep duration in seconds
    .min_lifetime: float   — minimum alive time before exit is allowed
    .exit_timeout: float   — maximum time allowed for shutdown cleanup

Skipped criteria (oracle: NOT VERIFIABLE):
  - "Advisor only reorders…; advisor failure leaves candidates in original order"
  - "Idle overhead is minimal (no busy-wait; sub-1% CPU intent)"
  - "SOLID, clean code (methods < 10 lines…)"
"""

import signal
import threading
import time

from hypothesis import given, settings, strategies as st


# ─────────────────────────────────────────────────────────────────────────────
# Fake collaborators  (derived from acceptance-criteria text, not from src/)
# ─────────────────────────────────────────────────────────────────────────────


class _CallLog:
    """Ordered record of symbolic call names for tick-order assertions."""

    def __init__(self):
        self.calls: list[str] = []

    def record(self, name: str) -> None:
        self.calls.append(name)


class FakePipeline:
    def __init__(self, state=None, log: _CallLog = None):
        self._state = state if state is not None else {"tick": 0}
        self._log = log or _CallLog()

    def step(self):
        self._log.record("pipeline.step")
        return self._state


class FakeDetect:
    def __init__(self, detection=None, log: _CallLog = None):
        self._detection = detection if detection is not None else {"candidates": []}
        self._log = log or _CallLog()

    def __call__(self, state):
        self._log.record("detect")
        return self._detection


class FakeAdvisor:
    def __init__(self, ranking=None, log: _CallLog = None):
        self._ranking = ranking if ranking is not None else []
        self._log = log or _CallLog()

    def rank(self, detection):
        self._log.record("advisor.rank")
        return self._ranking


class FakeEngine:
    def __init__(self, results=None, log: _CallLog = None):
        self._results = results if results is not None else []
        self._log = log or _CallLog()

    def execute(self, candidates, state):
        self._log.record("engine.execute")
        return self._results


class FakePortDiscoverer:
    def __init__(self, ports=None):
        from sentinel.domain.value_objects import PublishedPort, StackPorts  # noqa: PLC0415
        self.discovered: list[str] = []
        self._port_nums = ports if ports is not None else [8080]
        self._StackPorts = StackPorts
        self._PublishedPort = PublishedPort

    def discover(self, target: str):
        self.discovered.append(target)
        published = tuple(
            self._PublishedPort(host_ip="127.0.0.1", host_port=p, container_port=p)
            for p in self._port_nums
        )
        return self._StackPorts(stack=target, containers=(target,), ports=published)


class FakeWakeManager:
    def __init__(self):
        from sentinel.domain.value_objects import WakeRegistration  # noqa: PLC0415
        self._WakeRegistration = WakeRegistration
        self.registered: list = []
        self.stop_all_called: bool = False

    def register(self, registration) -> None:
        self.registered.append(registration)

    def stop_all(self) -> None:
        self.stop_all_called = True


class FakeClock:
    """
    Deterministic injectable clock.  sleep() advances the counter instantly so
    tests that exercise min-lifetime math do not block on wall-clock time.
    """

    def __init__(self):
        self._t: float = 0.0

    def monotonic(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._t += seconds


# ─────────────────────────────────────────────────────────────────────────────
# ActionResult stub  (kind / success / target — from criteria text)
# ─────────────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, kind: str, success: bool, target: str):
        self.kind = kind
        self.success = success
        self.target = target


def _stopped(target: str, *, success: bool = True) -> _Result:
    return _Result(kind="STOP_CONTAINER", success=success, target=target)


# ─────────────────────────────────────────────────────────────────────────────
# Config + daemon builder helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_config(**overrides):
    """Construct a minimal ServiceConfig for tests.  Remaining fields use defaults."""
    from sentinel.config import ServiceConfig  # noqa: PLC0415

    defaults = {"interval": 0.0, "min_lifetime": 0.0, "exit_timeout": 5.0}
    defaults.update(overrides)
    return ServiceConfig(**defaults)


def _build(
    *,
    log: _CallLog = None,
    config=None,
    state=None,
    detection=None,
    results=None,
    port_discoverer: FakePortDiscoverer = None,
    wake_manager: FakeWakeManager = None,
    advisor=None,
    clock: FakeClock = None,
):
    """
    Return (daemon, log, port_discoverer, wake_manager, clock) with all
    collaborators replaced by test doubles derived from criteria descriptions.
    """
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    log = log or _CallLog()
    config = config or _make_config()
    port_discoverer = port_discoverer or FakePortDiscoverer()
    wake_manager = wake_manager or FakeWakeManager()
    clock = clock or FakeClock()

    pipeline = FakePipeline(state=state, log=log)
    detect_fn = FakeDetect(detection=detection, log=log)
    engine = FakeEngine(results=results or [], log=log)
    adv = advisor if advisor is not None else FakeAdvisor(ranking=[], log=log)

    daemon = build_daemon(
        config,
        pipeline=pipeline,
        detect=detect_fn,
        advisor=adv,
        engine=engine,
        port_discoverer=port_discoverer,
        wake_manager=wake_manager,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return daemon, log, port_discoverer, wake_manager, clock


# ─────────────────────────────────────────────────────────────────────────────
# Tick order
# Criterion: one tick runs in order pipeline.step → detect → advisor.rank
#            → reorder → engine.execute
# ─────────────────────────────────────────────────────────────────────────────


def test_when_one_tick_runs_then_pipeline_step_is_the_first_call():
    daemon, log, *_ = _build()
    daemon.tick()
    assert log.calls[0] == "pipeline.step"


def test_when_one_tick_runs_then_detect_is_called_after_pipeline_step():
    daemon, log, *_ = _build()
    daemon.tick()
    assert log.calls.index("detect") > log.calls.index("pipeline.step")


def test_when_one_tick_runs_then_advisor_rank_is_called_after_detect():
    daemon, log, *_ = _build()
    daemon.tick()
    assert log.calls.index("advisor.rank") > log.calls.index("detect")


def test_when_one_tick_runs_then_engine_execute_is_called_after_advisor_rank():
    daemon, log, *_ = _build()
    daemon.tick()
    assert log.calls.index("engine.execute") > log.calls.index("advisor.rank")


def test_when_one_tick_runs_then_engine_execute_receives_the_exact_state_from_pipeline():
    """engine.execute second arg is the identical state object returned by pipeline.step."""
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    captured: dict = {}
    expected_state = {"pressure": 2, "disk_free_gb": 30}

    class CapturingEngine:
        def execute(self, candidates, state):
            captured["state"] = state
            return []

    log = _CallLog()
    clock = FakeClock()
    daemon = build_daemon(
        _make_config(),
        pipeline=FakePipeline(state=expected_state, log=log),
        detect=FakeDetect(log=log),
        advisor=FakeAdvisor(log=log),
        engine=CapturingEngine(),
        port_discoverer=FakePortDiscoverer(),
        wake_manager=FakeWakeManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    daemon.tick()

    assert captured.get("state") is expected_state


def test_when_one_tick_runs_then_advisor_rank_receives_the_exact_detection_from_detect():
    """advisor.rank argument is the identical detection object returned by detect."""
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    captured: dict = {}
    expected_detection = {"candidates": ["container_a", "container_b"]}

    class CapturingAdvisor:
        def rank(self, detection):
            captured["detection"] = detection
            return []

    log = _CallLog()
    clock = FakeClock()
    daemon = build_daemon(
        _make_config(),
        pipeline=FakePipeline(log=log),
        detect=FakeDetect(detection=expected_detection, log=log),
        advisor=CapturingAdvisor(),
        engine=FakeEngine(log=log),
        port_discoverer=FakePortDiscoverer(),
        wake_manager=FakeWakeManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    daemon.tick()

    assert captured.get("detection") is expected_detection


# ─────────────────────────────────────────────────────────────────────────────
# STOP_CONTAINER → port_discoverer.discover + wake_manager.register wiring
# Criterion: for each STOP_CONTAINER result with success=True,
#            daemon calls port_discoverer.discover(target) and wake_manager.register(...)
# ─────────────────────────────────────────────────────────────────────────────


def test_when_stop_container_success_true_then_port_discoverer_is_called_with_target():
    pd = FakePortDiscoverer()
    daemon, *_ = _build(results=[_stopped("clipcraft_api")], port_discoverer=pd)
    daemon.tick()
    assert "clipcraft_api" in pd.discovered


def test_when_stop_container_success_false_then_port_discoverer_is_not_called():
    pd = FakePortDiscoverer()
    daemon, *_ = _build(
        results=[_stopped("clipcraft_api", success=False)], port_discoverer=pd
    )
    daemon.tick()
    assert "clipcraft_api" not in pd.discovered


def test_when_stop_container_success_true_then_wake_manager_register_is_called_with_target_and_ports():
    wm = FakeWakeManager()
    pd = FakePortDiscoverer(ports=[8080, 3000])
    daemon, *_ = _build(
        results=[_stopped("clipcraft_api")], port_discoverer=pd, wake_manager=wm
    )
    daemon.tick()
    assert len(wm.registered) == 1
    reg = wm.registered[0]
    assert reg.stack == "clipcraft_api"
    assert sorted(p.host_port for p in reg.ports) == [3000, 8080]


def test_when_multiple_successful_stops_then_each_target_is_discovered_and_registered():
    wm = FakeWakeManager()
    pd = FakePortDiscoverer()
    daemon, *_ = _build(
        results=[_stopped("app_a"), _stopped("app_b")],
        port_discoverer=pd,
        wake_manager=wm,
    )
    daemon.tick()
    assert "app_a" in pd.discovered
    assert "app_b" in pd.discovered
    registered_targets = [r.stack for r in wm.registered]
    assert "app_a" in registered_targets
    assert "app_b" in registered_targets


def test_when_mixed_success_results_then_only_successful_stops_are_wired():
    wm = FakeWakeManager()
    pd = FakePortDiscoverer()
    daemon, *_ = _build(
        results=[_stopped("ok_app", success=True), _stopped("fail_app", success=False)],
        port_discoverer=pd,
        wake_manager=wm,
    )
    daemon.tick()
    assert "ok_app" in pd.discovered
    assert "fail_app" not in pd.discovered
    registered_targets = [r.stack for r in wm.registered]
    assert "ok_app" in registered_targets
    assert "fail_app" not in registered_targets


# ─────────────────────────────────────────────────────────────────────────────
# SIGTERM / stop-event shutdown
# Criterion: SIGTERM handler sets a stop event; loop finishes current tick;
#            writes state snapshot; calls wake_manager.stop_all()
# ─────────────────────────────────────────────────────────────────────────────


def test_when_stop_is_called_then_run_exits():
    daemon, *_ = _build()
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    assert not t.is_alive()


def test_when_stop_is_called_then_wake_manager_stop_all_is_called():
    wm = FakeWakeManager()
    daemon, *_ = _build(wake_manager=wm)
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    assert wm.stop_all_called


def test_when_stop_is_called_then_state_snapshot_is_written():
    """After shutdown, daemon.snapshot holds the last pipeline state (not None)."""
    daemon, *_ = _build()
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)
    assert getattr(daemon, "snapshot", None) is not None, (
        "expected daemon.snapshot to be set after clean shutdown"
    )


def test_when_stop_requested_mid_tick_then_current_tick_completes_before_exit():
    """
    The daemon must not abort mid-action.  When stop() is called while detect()
    is still running, the tick still reaches engine.execute before the loop exits.
    """
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    log = _CallLog()
    in_detect = threading.Event()
    allow_detect_continue = threading.Event()

    class BlockingDetect:
        def __call__(self, state):
            log.record("detect")
            in_detect.set()  # signal that we're mid-tick
            allow_detect_continue.wait()  # pause until the test unblocks us
            return {"candidates": []}

    clock = FakeClock()
    daemon = build_daemon(
        _make_config(),
        pipeline=FakePipeline(log=log),
        detect=BlockingDetect(),
        advisor=FakeAdvisor(log=log),
        engine=FakeEngine(log=log),
        port_discoverer=FakePortDiscoverer(),
        wake_manager=FakeWakeManager(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()

    assert in_detect.wait(timeout=2.0), "daemon never entered detect()"
    daemon.stop()  # request stop while detect is blocked
    allow_detect_continue.set()  # let the tick finish

    t.join(timeout=3.0)

    # The full tick sequence must appear in the call log
    for step in ("pipeline.step", "detect", "advisor.rank", "engine.execute"):
        assert step in log.calls, f"tick was aborted before '{step}'"


# ─────────────────────────────────────────────────────────────────────────────
# Minimum process lifetime floor
# Criterion: total process lifetime is never below ServiceConfig.min_lifetime
# ─────────────────────────────────────────────────────────────────────────────


def test_when_min_lifetime_set_and_stop_requested_immediately_then_daemon_waits_for_floor():
    """
    With min_lifetime=0.2 (fake seconds), an immediate stop() must not let
    the daemon exit until the fake clock has reached >= 0.2.
    """
    clock = FakeClock()
    config = _make_config(min_lifetime=0.2, interval=0.0)
    daemon, *_ = _build(config=config, clock=clock)

    exit_clock: list[float] = []

    def run_and_capture():
        daemon.run()
        exit_clock.append(clock.monotonic())

    t = threading.Thread(target=run_and_capture, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)

    assert exit_clock, "daemon never exited"
    assert exit_clock[0] >= 0.2, (
        f"daemon exited at fake-clock t={exit_clock[0]:.4f}, "
        "before min_lifetime floor of 0.2"
    )


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=10, deadline=5000)
def test_when_min_lifetime_is_any_non_negative_value_then_exit_is_not_before_floor(
    min_lifetime: float,
):
    """
    Invariant (min-lifetime floor): for every valid non-negative min_lifetime,
    the fake-clock reading at exit must be >= min_lifetime.
    """
    clock = FakeClock()
    config = _make_config(min_lifetime=min_lifetime, interval=0.0)
    daemon, *_ = _build(config=config, clock=clock)

    exit_clock: list[float] = []

    def run_and_capture():
        daemon.run()
        exit_clock.append(clock.monotonic())

    t = threading.Thread(target=run_and_capture, daemon=True)
    t.start()
    time.sleep(0.05)
    daemon.stop()
    t.join(timeout=3.0)

    if exit_clock:
        assert exit_clock[0] >= min_lifetime, (
            f"min_lifetime={min_lifetime:.4f} but daemon exited at fake t={exit_clock[0]:.4f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sliced interval sleep
# Criterion: interval sleep is sliced so SIGTERM is honoured promptly
#            (not a single long sleep(interval))
# ─────────────────────────────────────────────────────────────────────────────


def test_when_stop_called_during_inter_tick_sleep_then_loop_exits_promptly():
    """
    With a 60 s nominal interval, stop() must be honoured in well under 60 s
    (real wall-clock time).  If sleep is not sliced the thread would block for
    a full minute.

    Real sleep/monotonic are used so we actually measure wall-clock promptness.
    """
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    config = _make_config(interval=60.0, min_lifetime=0.0)
    log = _CallLog()
    daemon = build_daemon(
        config,
        pipeline=FakePipeline(log=log),
        detect=FakeDetect(log=log),
        advisor=FakeAdvisor(log=log),
        engine=FakeEngine(log=log),
        port_discoverer=FakePortDiscoverer(),
        wake_manager=FakeWakeManager(),
        monotonic=time.monotonic,
        sleep=time.sleep,
    )

    wall_start = time.monotonic()
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()
    time.sleep(0.1)  # let daemon enter the inter-tick sleep
    daemon.stop()
    t.join(timeout=5.0)

    elapsed = time.monotonic() - wall_start
    assert not t.is_alive(), "daemon thread did not exit after stop()"
    assert elapsed < 5.0, (
        f"daemon took {elapsed:.1f}s to honour stop() — "
        "the 60s interval sleep was not sliced"
    )


# ─────────────────────────────────────────────────────────────────────────────
# build_daemon composition root — deferred OS imports
# Criterion: build_daemon(config, *, ...overrides) composition root defers OS imports
# ─────────────────────────────────────────────────────────────────────────────


def test_when_daemon_module_is_imported_then_no_sigterm_handler_is_registered_as_side_effect():
    """
    Importing sentinel.service.daemon must not register any SIGTERM handler at
    module load time.  OS signal registration is deferred to run().
    """
    import importlib
    import sys

    original = signal.getsignal(signal.SIGTERM)
    try:
        mod_name = "sentinel.service.daemon"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            importlib.import_module(mod_name)
        after = signal.getsignal(signal.SIGTERM)
        assert after is original, (
            "importing sentinel.service.daemon changed the SIGTERM handler; "
            "OS signal registration must be deferred to build_daemon() or run()"
        )
    finally:
        signal.signal(signal.SIGTERM, original)


def test_when_build_daemon_is_called_then_returned_object_exposes_run_stop_tick():
    """build_daemon returns a Daemon that exposes the three required methods."""
    daemon, *_ = _build()
    assert callable(getattr(daemon, "run", None)), "Daemon must have callable run()"
    assert callable(getattr(daemon, "stop", None)), "Daemon must have callable stop()"
    assert callable(getattr(daemon, "tick", None)), "Daemon must have callable tick()"
