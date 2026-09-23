"""Quota exhaustion detection and a session-scoped circuit breaker.

When a model credential runs out of balance (e.g. GLM-5.3-Flash credits on
z.ai), the provider returns an auth/quota error.  Without handling, Maestro
surfaces a dead-end error turn and picks the same dead model again on the
next turn.  This module classifies such errors at the runtime layer and
remembers the exhausted model for the lifetime of the process, so selection
layers can exclude it and fall back to the next capable model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Balance-ish phrases in provider error bodies.  Deliberately excludes bare
# "exceeded"/"rate limit" so plain throttling and context-overflow errors are
# not misclassified as quota exhaustion.
_BALANCE_RE = re.compile(
    r"balance|quota|credit|insufficient|arrears|depleted|usage limit",
    re.IGNORECASE,
)

# Session-scoped circuit breaker: model ids and runtime identifiers that have
# failed with a quota error.  Process lifetime is enough — a topped-up balance
# implies a new session anyway.
_exhausted: set[str] = set()


def classify_quota_error(exc: BaseException) -> str | None:
    """Return a short reason when *exc* means the model/credential is out of service.

    Returns None for transient/transport errors (the caller keeps its normal
    retry behavior), for plain rate limiting, and for request-shape errors
    such as context overflow.
    """
    status = getattr(exc, "status_code", None)

    if status in (401, 403):
        return f"authentication failed (HTTP {status})"
    if status == 402:
        return "payment required (HTTP 402)"
    if status is not None and _BALANCE_RE.search(_error_text(exc)):
        return "balance or quota exhausted"
    return None


def _error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body:
        parts.append(str(body))
    return " ".join(parts)


def mark_exhausted(key: str | None) -> None:
    """Record *key* (model id or runtime identifier) as quota-exhausted."""
    if key:
        _exhausted.add(key)


def is_exhausted(key: str | None) -> bool:
    return bool(key) and key in _exhausted


def exhausted() -> set[str]:
    """Return a copy of the current exhausted-model keys."""
    return set(_exhausted)


def clear() -> None:
    _exhausted.clear()


def load_fallback_order() -> list[str]:
    """Read ``routing.fallback_order`` from the user capabilities file.

    The list is an explicit, deterministic fallback chain of model ids or
    runtime identifiers.  Absent config means fully automatic fallback.
    """
    path = Path.home() / ".open-maestro" / "capabilities.yaml"
    if not path.exists():
        return []
    try:
        import yaml

        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        order = (data.get("routing") or {}).get("fallback_order") or []
        return [str(entry) for entry in order]
    except Exception as exc:
        logger.debug("Failed to load routing.fallback_order: %s", exc)
        return []
