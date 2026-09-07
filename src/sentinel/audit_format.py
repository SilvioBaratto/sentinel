"""Single owner of the on-disk audit record format.

The writer (``execute.audit.RotatingAuditLogger``) and the reader
(``service.status_provider``) previously each defined the format independently —
an f-string on one side, a regex on the other.  They disagreed: the writer never
emitted ``kind``, so the reader hardcoded ``STOP_CONTAINER`` for every row, and
``timestamp`` and ``detail`` were dropped entirely, discarding the very field
that says *why* an action failed.

One module owns it now.  ``encode``/``decode`` are pure and mutually inverse;
``render`` produces the human line the CLI prints.

Format is JSON Lines — the file is already named ``sentinel.audit.jsonl``.

Two encoding invariants, both load-bearing:

* ``ensure_ascii=True``.  ``str.splitlines()`` (used by the tail reader) splits
  on U+2028/U+2029 as well as ``\\n``, so a filename containing either would
  break one record into two.  Escaping every non-ASCII codepoint makes that
  impossible.  It also sidesteps surrogates from undecodable APFS filenames,
  which would otherwise raise inside ``emit``.
* JSON string escaping neutralises newline injection.  Under the old key=value
  format a file named ``x\\ntarget=/ success=True`` forged an audit row; the
  audit log is security-relevant, so a target name must never be able to
  synthesise or truncate a record.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sentinel.domain.value_objects import (
    ActionKind,
    ActionResult,
    AuditRecord,
    ExecutionMode,
    Reversibility,
)
from sentinel.fmt import format_bytes

__all__ = ["encode", "decode", "render", "LEGACY_AUDIT_RE"]

# The pre-JSON key=value format, kept so an existing log file still parses.
# Anchored with fullmatch at the call site: the old unanchored `search` let a
# crafted target name inject a second, attacker-chosen record into any line.
LEGACY_AUDIT_RE = re.compile(
    r"target=(?P<target>.+?)\s+size=.+?\s+"
    r"reversibility=(?P<rev>\w+)\s+mode=\w+\s+success=(?P<ok>\w+)"
)


def encode(rec: AuditRecord) -> str:
    """Serialise one AuditRecord to a single-line JSON object."""
    return json.dumps(
        {
            "ts": float(rec.timestamp),
            "kind": _enum_value(rec.kind),
            "target": str(rec.target),
            "success": bool(rec.success),
            "reversibility": _enum_value(rec.reversibility),
            "bytes_freed": int(rec.bytes_freed),
            # Redundant with bytes_freed, and deliberately so: the acceptance
            # criterion requires each line to carry a human-readable size
            # (e.g. "1.2 GB"), and a log you can read with `tail` is worth the
            # duplicated field. bytes_freed stays authoritative for decode().
            "size": format_bytes(int(rec.bytes_freed)),
            "mode": _enum_value(rec.mode),
            "detail": str(rec.detail or ""),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def decode(line: str) -> ActionResult | None:
    """Parse one log line into an ActionResult; None when unparseable.

    Tries JSON first, then the legacy key=value format.  Never raises.
    """
    text = line.strip()
    if not text:
        return None
    return _decode_json(text) or _decode_legacy(text)


def render(line: str) -> str:
    """Human-readable one-line rendering for ``sentinel status``.

    Unparseable lines are passed through verbatim rather than hidden — a
    corrupt log should look corrupt, not empty.
    """
    payload = _loads(line)
    if payload is None:
        return _one_line(line.strip())
    ok = "ok " if payload.get("success") else "FAIL"
    kind = _one_line(str(payload.get("kind", "?")))
    target = _one_line(str(payload.get("target", "?")))
    size = format_bytes(int(payload.get("bytes_freed") or 0))
    detail = _one_line(str(payload.get("detail") or ""))
    line_out = f"{ok} {kind} {target} ({size})"
    return f"{line_out} — {detail}" if detail else line_out


# ── internals ─────────────────────────────────────────────────────────────────


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


# Line/paragraph separators and C0 controls, all of which either split a line
# under str.splitlines() or let a target name repaint the terminal.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]")


def _one_line(text: str) -> str:
    """Collapse anything that could forge a second row in rendered output.

    encode() escapes these on the way to disk; render() must do the same on the
    way to a terminal, or a crafted filename fabricates audit lines on screen.
    """
    return _CONTROL_RE.sub("�", text)


def _loads(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line.strip())
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _decode_json(text: str) -> ActionResult | None:
    payload = _loads(text)
    if payload is None or "target" not in payload:
        return None
    return ActionResult(
        kind=_coerce(ActionKind, payload.get("kind"), ActionKind.TRASH),
        target=str(payload["target"]),
        success=bool(payload.get("success")),
        reversibility=_coerce(
            Reversibility, payload.get("reversibility"), Reversibility.PERMANENT
        ),
        bytes_freed=int(payload.get("bytes_freed") or 0),
        detail=str(payload.get("detail") or ""),
    )


def _decode_legacy(text: str) -> ActionResult | None:
    m = LEGACY_AUDIT_RE.fullmatch(text)
    if not m:
        return None
    return ActionResult(
        kind=ActionKind.STOP_CONTAINER,  # the only kind the old writer emitted
        target=m.group("target"),
        success=m.group("ok").lower() == "true",
        reversibility=_coerce(Reversibility, m.group("rev"), Reversibility.PERMANENT),
    )


def _coerce(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(str(value).lower())
    except (ValueError, AttributeError):
        return default


def mode_of(line: str) -> ExecutionMode | None:
    """Best-effort ExecutionMode for a record; None when absent or unparseable."""
    payload = _loads(line)
    if payload is None:
        return None
    return _coerce(ExecutionMode, payload.get("mode"), None)
