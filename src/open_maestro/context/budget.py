"""Context budget and snapshot types for long-running agent sessions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextBudget:
    """Token budget and alert thresholds for a session.

    Defaults are tuned for a ~200k context window; users can override via
    configuration or CLI flags.
    """

    max_context_tokens: int | None = 200000
    warning_threshold: float | None = 0.70
    critical_threshold: float | None = 0.90

    def __post_init__(self) -> None:
        if self.max_context_tokens is None:
            self.max_context_tokens = 200000
        if self.warning_threshold is None:
            self.warning_threshold = 0.70
        if self.critical_threshold is None:
            self.critical_threshold = 0.90

    @property
    def warning_tokens(self) -> int:
        return int(self.max_context_tokens * self.warning_threshold)

    @property
    def critical_tokens(self) -> int:
        return int(self.max_context_tokens * self.critical_threshold)
