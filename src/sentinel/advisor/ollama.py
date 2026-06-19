"""Ollama advisor — rank + explain reclaim candidates, offline-graceful.

Uses stdlib ``urllib.request`` only (no new dependency). Any failure —
connection refused, timeout, non-200, malformed body — falls back to an
identity ranking so core Sentinel logic is never blocked.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Callable

from sentinel.config import AdvisorConfig
from sentinel.domain.value_objects import AdvisorRanking

_LOG = logging.getLogger(__name__)
_CHAT_PATH = "/api/chat"

_PROMPT = (
    "Rank these idle resource candidates from highest to lowest reclaim priority: {names}. "
    'Reply ONLY with valid JSON: {{"ranking": ["name1", ...], '
    '"explanations": {{"name1": "reason", ...}}}}'
)


# ── candidate extraction ───────────────────────────────────────────────────────


def _candidates(detection: object) -> tuple[str, ...]:
    """Duck-typed extraction: ``DetectionResult`` or any object with ``.candidates``."""
    if hasattr(detection, "candidates"):
        return tuple(detection.candidates)  # type: ignore[union-attr]
    names: list[str] = []
    for p in getattr(detection, "processes", ()):
        names.append(p.info.name)
    for c in getattr(detection, "containers", ()):
        names.append(c.name)
    return tuple(names)


def _identity(targets: tuple[str, ...]) -> AdvisorRanking:
    return AdvisorRanking(ordered_targets=targets, explanations={})


# ── request helpers ────────────────────────────────────────────────────────────


def _body(config: AdvisorConfig, targets: tuple[str, ...]) -> bytes:
    return json.dumps(
        {
            "model": config.model,
            "keep_alive": config.keep_alive,
            "messages": [
                {"role": "user", "content": _PROMPT.format(names=", ".join(targets))}
            ],
        }
    ).encode()


def _request(config: AdvisorConfig, targets: tuple[str, ...]) -> urllib.request.Request:
    return urllib.request.Request(
        url=f"{config.base_url}{_CHAT_PATH}",
        data=_body(config, targets),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


# ── response parsing ───────────────────────────────────────────────────────────


def _content(outer: dict[str, Any]) -> str:
    """Pull model text from /api/chat or /api/generate envelope."""
    if "message" in outer:
        return outer["message"]["content"]
    if "response" in outer:
        return outer["response"]
    raise KeyError("no content field in response")


def _parse(raw: bytes) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return ``(ordered_targets, explanations)`` or raise on any parse error."""
    outer = json.loads(raw)
    inner = json.loads(_content(outer))
    return tuple(inner["ranking"]), dict(inner.get("explanations", {}))


# ── advisor implementations ────────────────────────────────────────────────────


class NullAdvisor:
    """Identity passthrough — no network call, always safe."""

    def rank(self, detection: object) -> AdvisorRanking:
        return _identity(_candidates(detection))


class OllamaAdvisor:
    """Rank candidates via Ollama REST; falls back to identity on any failure."""

    def __init__(
        self,
        config: AdvisorConfig,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._config = config
        self._opener = opener

    def rank(self, detection: object) -> AdvisorRanking:
        targets = _candidates(detection)
        try:
            return self._call(targets)
        except Exception as exc:
            _LOG.debug("advisor fallback: %s", exc)
            return _identity(targets)

    def _call(self, targets: tuple[str, ...]) -> AdvisorRanking:
        req = _request(self._config, targets)
        with self._opener(req, timeout=self._config.request_timeout) as resp:
            ranking, explanations = _parse(resp.read())
        return AdvisorRanking(ordered_targets=ranking, explanations=explanations)


# ── factory ────────────────────────────────────────────────────────────────────


def build_advisor(
    config: AdvisorConfig,
    *,
    _url_opener: object | None = None,
) -> NullAdvisor | OllamaAdvisor:
    """``NullAdvisor`` when disabled (no egress); ``OllamaAdvisor`` when enabled."""
    if not config.enabled:
        return NullAdvisor()
    opener: Any = _url_opener or urllib.request.urlopen
    return OllamaAdvisor(config, opener=opener)
