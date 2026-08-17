"""Latency tracking and scoring for model arbitration.

Maestro picks the cheapest capable model, but only if it is not much slower than
the fastest alternative.  This module maintains a small on-disk cache of measured
tokens-per-second for each model and falls back to capability-registry latency
hints when no measurement exists yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from open_maestro.config.capabilities import Capabilities, LatencyHint

logger = logging.getLogger(__name__)

# Latency hint fallback scores.  These are arbitrary units; only ratios matter.
_LATENCY_HINT_SCORES: dict[LatencyHint, float] = {
    LatencyHint.LOW: 100.0,
    LatencyHint.MEDIUM: 200.0,
    LatencyHint.HIGH: 400.0,
}

# Baseline: time to generate 1000 tokens, in the same arbitrary units.
_TOKENS_PER_SECOND_BASELINE = 50.0


@dataclass
class LatencyCache:
    """Measured throughput per model, persisted as YAML."""

    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> LatencyCache:
        if path is None:
            path = Path.home() / ".open-maestro" / "latency.yaml"
        path = path.expanduser()
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return cls(measurements=dict(data.get("measurements", {})))
        except Exception as exc:
            logger.warning("Failed to load latency cache %s: %s", path, exc)
            return cls()

    def save(self, path: Path | None = None) -> None:
        if path is None:
            path = Path.home() / ".open-maestro" / "latency.yaml"
        path = path.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                yaml.safe_dump({"measurements": self.measurements}, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save latency cache %s: %s", path, exc)

    def record(
        self,
        model_id: str,
        *,
        tokens_per_second: float,
        alpha: float = 0.3,
    ) -> None:
        """Update the cached tokens-per-second with an exponential moving average."""
        if tokens_per_second <= 0:
            return
        existing = self.measurements.get(model_id, {})
        current = existing.get("tokens_per_second")
        if current is None:
            new_tps = tokens_per_second
        else:
            new_tps = alpha * tokens_per_second + (1 - alpha) * float(current)
        self.measurements[model_id] = {"tokens_per_second": new_tps}

    def tokens_per_second(self, model_id: str) -> float | None:
        entry = self.measurements.get(model_id)
        if entry is None:
            return None
        return float(entry.get("tokens_per_second", 0)) or None


def score_from_measurement(tokens_per_second: float) -> float:
    """Convert tokens-per-second into a latency score.

    Score is the time to generate 1000 tokens relative to a 50 tok/s baseline.
    Lower is faster.
    """
    return 1000.0 / max(tokens_per_second, 1.0) * _TOKENS_PER_SECOND_BASELINE


def score_from_capabilities(capabilities: Capabilities) -> float:
    """Return a fallback latency score from the model's declared latency hint."""
    return _LATENCY_HINT_SCORES.get(
        capabilities.latency_hint, _LATENCY_HINT_SCORES[LatencyHint.MEDIUM]
    )


def model_latency_score(
    model_id: str,
    capabilities: Capabilities,
    cache: LatencyCache | None = None,
) -> float:
    """Return a numeric latency score for *model_id*.

    Uses a cached measurement if present, otherwise falls back to the declared
    latency hint.
    """
    if cache is None:
        cache = LatencyCache.load()
    measured = cache.tokens_per_second(model_id)
    if measured is not None:
        return score_from_measurement(measured)
    return score_from_capabilities(capabilities)


def record_result(
    model_id: str,
    duration_ms: int | None,
    output_tokens: int | None,
    cache: LatencyCache | None = None,
) -> None:
    """Record latency information from a completed run.

    Does nothing if *duration_ms* or *output_tokens* are missing or zero.
    """
    if not duration_ms or not output_tokens:
        return
    seconds = duration_ms / 1000.0
    if seconds <= 0:
        return
    tps = output_tokens / seconds
    if cache is None:
        cache = LatencyCache.load()
    cache.record(model_id, tokens_per_second=tps)
    cache.save()
