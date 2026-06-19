"""State machine: consumes CandidateSignal, emits canonical SentinelState.

Pure and stateful — no I/O, no executors. The debounce and precedence logic
live entirely in the threshold engine upstream; this machine's only job is to
record the accepted state and expose it for downstream cycles to gate on.

Design note: SilvioBaratto (2026-06-19) flagged that collapsing DISK_LOW into
a single-enum precedence (CRITICAL > DISK_LOW) masks the disk-low condition
when memory pressure is simultaneously CRITICAL. The precedence decision already
lives in the threshold engine (#5); if orthogonal representation is needed for
Cycle 3 this machine should stay a dumb consumer while the engine emits a richer
signal (e.g. a composite value). No premature abstraction here until Cycle 3
clarifies the executor contract.
"""

from __future__ import annotations

from sentinel.domain.value_objects import CandidateSignal, SentinelState


class SentinelStateMachine:
    """Pure state consumer implementing the StateMachine protocol.

    Starts at NORMAL. Each call to transition() applies the proposed state from
    the incoming CandidateSignal and returns it. The state property is read-only:
    polling it never drives a transition.
    """

    def __init__(self) -> None:
        self._current: SentinelState = SentinelState.NORMAL

    @property
    def state(self) -> SentinelState:
        return self._current

    def transition(self, signal: CandidateSignal) -> SentinelState:
        self._current = signal.proposed_state
        return self._current
