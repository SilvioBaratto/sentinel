"""RotatingAuditLogger — one entry per action, rotating log file, fail-safe.

Implements AuditLogger: record(AuditRecord) -> None.
Never raises — a logging/notification failure must never break the executor.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from sentinel.domain.value_objects import AuditRecord
from sentinel.fmt import format_bytes


class RotatingAuditLogger:
    """Write one audit line per action to a rotating log file.

    Constructor forms:
    - RotatingAuditLogger(handler=h)          — inject handler (unit tests)
    - RotatingAuditLogger(log_path=p, ...)    — real RotatingFileHandler (production)
    """

    def __init__(
        self,
        handler: logging.Handler | None = None,
        *,
        log_path: Path | str | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backups: int = 5,
    ) -> None:
        if handler is None:
            if log_path is None:
                raise ValueError("Provide either handler= or log_path=")
            handler = logging.handlers.RotatingFileHandler(
                str(log_path), maxBytes=max_bytes, backupCount=backups
            )
        # Unique logger per instance — avoids shared state between test instances.
        self._logger = logging.getLogger(f"sentinel.audit.{id(self)}")
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    def record(self, record: AuditRecord) -> None:
        try:
            self._logger.info(self._format(record))
        except Exception:
            pass

    def _format(self, rec: AuditRecord) -> str:
        size = format_bytes(rec.bytes_freed)
        return (
            f"target={rec.target} size={size} "
            f"reversibility={rec.reversibility.value} "
            f"mode={rec.mode.value} success={rec.success}"
        )
