"""Tests for RotatingAuditLogger — authored from Issue #27 acceptance criteria.

Red-phase TDD: written before any implementation exists, source-blind.

Assumptions (documented where criteria text is ambiguous):
- RotatingAuditLogger lives in sentinel.execute.audit.
- Unit-test seam: RotatingAuditLogger(handler=<logging.Handler>) — the criterion
  text "unit tests inject the writer/handler so they stay pure" implies the
  constructor accepts a logging.Handler directly.
- Integration seam: RotatingAuditLogger(log_path=Path, max_bytes=int, backups=int) —
  the production path that creates a real RotatingFileHandler; used by the rotation
  integration test with tmp_path.
- AuditRecord is a new value object in sentinel.domain.value_objects with fields:
    target: str, bytes_freed: int, reversible: bool, mode: str, success: bool
- AuditLogger is a Protocol in sentinel.domain.protocols with
    record(record: AuditRecord) -> None
- Human-readable size uses SI decimal units: 1_200_000_000 bytes -> "1.2 GB",
  matching the criterion's own example "e.g. '1.2 GB'".
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from hypothesis import given, strategies as st

from sentinel.domain.value_objects import (
    ActionKind,
    AuditRecord,
    ExecutionMode,
    Reversibility,
)
from sentinel.execute.audit import RotatingAuditLogger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GiB = 1024**3


# ---------------------------------------------------------------------------
# Spy handler — captures formatted lines in-memory (no filesystem I/O)
# ---------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    """Records the formatted text of every emit() call."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


# ---------------------------------------------------------------------------
# Broken handler — always raises on emit, for swallow tests
# ---------------------------------------------------------------------------


class _BrokenHandler(logging.Handler):
    """Simulates a handler that fails on every write (e.g. disk full)."""

    def emit(self, record: logging.LogRecord) -> None:
        raise OSError("simulated write failure")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_record(
    target: str = "/tmp/sentinel/cache",
    bytes_freed: int = 1_200_000_000,
    reversible: bool = True,
    mode: str = "auto",
    success: bool = True,
) -> AuditRecord:
    return AuditRecord(
        timestamp=0.0,
        kind=ActionKind.TRASH,
        target=target,
        success=success,
        reversibility=Reversibility.REVERSIBLE
        if reversible
        else Reversibility.PERMANENT,
        bytes_freed=bytes_freed,
        mode=ExecutionMode(mode),
        detail="",
    )


def _make_unit_logger() -> tuple[RotatingAuditLogger, _CapturingHandler]:
    handler = _CapturingHandler()
    return RotatingAuditLogger(handler=handler), handler


# ===========================================================================
# Criterion 1a — AuditLogger protocol compliance
# "record(record: AuditRecord) -> None"
# ===========================================================================


class TestRotatingAuditLoggerProtocol:
    def test_when_record_called_then_return_value_is_none(self) -> None:
        """record() must return None — the AuditLogger protocol signature."""
        logger, _ = _make_unit_logger()
        result = logger.record(_make_record())
        assert result is None

    def test_when_rotating_audit_logger_created_then_it_satisfies_audit_logger_protocol(
        self,
    ) -> None:
        """RotatingAuditLogger must structurally satisfy AuditLogger (has 'record' method)."""
        logger, _ = _make_unit_logger()
        assert hasattr(logger, "record")
        assert callable(logger.record)


# ===========================================================================
# Criterion 1b — logged line contains all required fields
# "writes one line containing target, human-readable size, reversibility, mode, success"
# ===========================================================================


class TestRotatingAuditLoggerOutput:
    def test_when_record_is_logged_then_exactly_one_line_is_emitted(self) -> None:
        """One record() call must produce exactly one log line."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record())
        assert len(handler.lines) == 1

    def test_when_record_is_logged_then_output_contains_target(self) -> None:
        """The logged line must include the target path verbatim."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(target="/var/folders/abc/chrome_cache"))
        assert "/var/folders/abc/chrome_cache" in handler.lines[0]

    def test_when_record_is_logged_then_output_contains_human_readable_size(
        self,
    ) -> None:
        """The logged line must include a SI decimal human-readable size.

        Assumption: 1_200_000_000 bytes is rendered as '1.2 GB', following the
        criterion's exact example "e.g. '1.2 GB'".
        """
        logger, handler = _make_unit_logger()
        logger.record(_make_record(bytes_freed=1_200_000_000))
        assert "1.2 GB" in handler.lines[0]

    def test_when_record_is_logged_then_output_contains_reversibility_indicator(
        self,
    ) -> None:
        """The logged line must convey reversibility (criterion word: 'reversibility')."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(reversible=True))
        assert "reversible" in handler.lines[0].lower()

    def test_when_record_is_logged_then_output_contains_mode(self) -> None:
        """The logged line must include the execution mode string."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(mode="confirm"))
        assert "confirm" in handler.lines[0]

    def test_when_record_is_logged_then_output_contains_success_indicator(
        self,
    ) -> None:
        """The logged line must indicate success or failure."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(success=True))
        line = handler.lines[0].lower()
        assert "success" in line or "true" in line or "ok" in line

    def test_when_record_logged_with_irreversible_action_then_output_reflects_that(
        self,
    ) -> None:
        """reversible=False must also be present in the log line in some form."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(reversible=False))
        line = handler.lines[0].lower()
        assert "irreversible" in line or "permanent" in line or "false" in line

    def test_when_record_logged_with_failed_action_then_output_reflects_that(
        self,
    ) -> None:
        """success=False must also be reflected in the log line."""
        logger, handler = _make_unit_logger()
        logger.record(_make_record(success=False))
        line = handler.lines[0].lower()
        assert "fail" in line or "false" in line or "error" in line

    # ---- Property: target always appears verbatim in the emitted line ----

    @given(
        target=st.text(min_size=1, max_size=200).filter(
            lambda s: "\n" not in s and "\r" not in s
        )
    )
    def test_when_target_is_any_printable_string_then_it_appears_in_log_line(
        self, target: str
    ) -> None:
        """Invariant: for any non-empty, single-line target, it must appear verbatim.

        Derived from criterion: 'writes one line containing target'.
        """
        logger, handler = _make_unit_logger()
        logger.record(_make_record(target=target))
        assert target in handler.lines[0]


# ===========================================================================
# Criterion 1c — swallows writer exceptions (never raises)
# "swallows writer exceptions (never raises)"
# ===========================================================================


class TestRotatingAuditLoggerSwallowsExceptions:
    def test_when_handler_raises_then_record_does_not_propagate_exception(
        self,
    ) -> None:
        """A broken handler must not cause record() to raise."""
        logger = RotatingAuditLogger(handler=_BrokenHandler())
        logger.record(_make_record())  # must not raise

    # ---- Property: never raises for any bytes_freed, even when handler is broken ----

    @given(bytes_freed=st.integers(min_value=0, max_value=10 * _GiB))
    def test_when_bytes_freed_varies_and_handler_is_broken_then_record_never_raises(
        self, bytes_freed: int
    ) -> None:
        """Invariant: record() swallows the handler exception for every valid bytes_freed.

        Derived from criterion: 'swallows writer exceptions (never raises)'.
        """
        logger = RotatingAuditLogger(handler=_BrokenHandler())
        logger.record(_make_record(bytes_freed=bytes_freed))  # must not raise


# ===========================================================================
# Criterion 2 — rotation: N records produce ≤ backups+1 files on disk
# "with a tiny max_bytes, N records produce ≤ backups+1 files (real tmp_path
#  integration test)"
# ===========================================================================


class TestRotatingAuditLoggerRotation:
    def test_when_many_records_written_then_file_count_does_not_exceed_backups_plus_one(
        self, tmp_path: Path
    ) -> None:
        """Integration test — exercises real RotatingFileHandler via filesystem.

        Writes 20 records to a logger configured with max_bytes=128 and backups=3
        to force repeated rotation. The total number of files on disk (base log +
        rotated backups) must never exceed backups + 1 = 4.

        Assumption: the production constructor is
            RotatingAuditLogger(log_path=Path, max_bytes=int, backups=int)
        and creates a real RotatingFileHandler internally.
        """
        log_path = tmp_path / "audit.log"
        backups = 3
        logger = RotatingAuditLogger(log_path=log_path, max_bytes=128, backups=backups)

        for i in range(20):
            logger.record(
                _make_record(target=f"/tmp/sentinel/item_{i}", bytes_freed=i * 1024)
            )

        produced = list(tmp_path.glob("audit.log*"))
        assert len(produced) <= backups + 1
